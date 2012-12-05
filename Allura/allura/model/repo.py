import os
import re
import sys
import logging
from hashlib import sha1
from itertools import chain
from datetime import datetime
from collections import defaultdict, OrderedDict
from difflib import SequenceMatcher, unified_diff

from pylons import c
import pymongo.errors

from ming import Field, collection, Index
from ming import schema as S
from ming.base import Object
from ming.utils import LazyProperty
from ming.orm import mapper, session

from allura.lib import utils
from allura.lib import helpers as h

from .auth import User
from .session import main_doc_session, project_doc_session
from .session import repository_orm_session

log = logging.getLogger(__name__)

# Some schema types
SUser = dict(name=str, email=str, date=datetime)
SObjType=S.OneOf('blob', 'tree', 'submodule')

# Used for when we're going to batch queries using $in
QSIZE = 100
README_RE = re.compile('^README(\.[^.]*)?$', re.IGNORECASE)
VIEWABLE_EXTENSIONS = ['.php','.py','.js','.java','.html','.htm','.yaml','.sh',
    '.rb','.phtml','.txt','.bat','.ps1','.xhtml','.css','.cfm','.jsp','.jspx',
    '.pl','.php4','.php3','.rhtml','.svg','.markdown','.json','.ini','.tcl','.vbs','.xsl']

DIFF_SIMILARITY_THRESHOLD = .5  # used for determining file renames

# Basic commit information
# One of these for each commit in the physical repo on disk. The _id is the
# hexsha of the commit (for Git and Hg).
CommitDoc = collection(
    'repo_ci', main_doc_session,
    Field('_id', str),
    Field('tree_id', str),
    Field('committed', SUser),
    Field('authored', SUser),
    Field('message', str),
    Field('parent_ids', [str], index=True),
    Field('child_ids', [str], index=True),
    Field('repo_ids', [ S.ObjectId() ], index=True))

# Basic tree information (also see TreesDoc)
TreeDoc = collection(
    'repo_tree', main_doc_session,
    Field('_id', str),
    Field('tree_ids', [dict(name=str, id=str)]),
    Field('blob_ids', [dict(name=str, id=str)]),
    Field('other_ids', [dict(name=str, id=str, type=SObjType)]))

LastCommitDoc_old = collection(
    'repo_last_commit', project_doc_session,
    Field('_id', str),
    Field('object_id', str, index=True),
    Field('name', str),
    Field('commit_info', dict(
        id=str,
        date=datetime,
        author=str,
        author_email=str,
        author_url=str,
        shortlink=str,
        summary=str)))

# Information about the last commit to touch a tree
LastCommitDoc = collection(
    'repo_last_commit', main_doc_session,
    Field('_id', S.ObjectId()),
    Field('commit_ids', [str]),
    Field('path', str),
    Index('commit_ids', 'path'),
    Field('entries', [dict(
        type=str,
        name=str,
        commit_info=dict(
            id=str,
            date=datetime,
            author=str,
            author_email=str,
            author_url=str,
            shortlink=str,
            summary=str))]))

# List of all trees contained within a commit
# TreesDoc._id = CommitDoc._id
# TreesDoc.tree_ids = [ TreeDoc._id, ... ]
TreesDoc = collection(
    'repo_trees', main_doc_session,
    Field('_id', str),
    Field('tree_ids', [str]))

# Information about which things were added/removed in  commit
# DiffInfoDoc._id = CommitDoc._id
DiffInfoDoc = collection(
    'repo_diffinfo', main_doc_session,
    Field('_id', str),
    Field(
        'differences',
        [ dict(name=str, lhs_id=str, rhs_id=str)]))

# List of commit runs (a run is a linear series of single-parent commits)
# CommitRunDoc.commit_ids = [ CommitDoc._id, ... ]
CommitRunDoc = collection(
    'repo_commitrun', main_doc_session,
    Field('_id', str),
    Field('parent_commit_ids', [str], index=True),
    Field('commit_ids', [str], index=True),
    Field('commit_times', [datetime]))

class RepoObject(object):

    def __repr__(self): # pragma no cover
        return '<%s %s>' % (
            self.__class__.__name__, self._id)

    def primary(self):
        return self

    def index_id(self):
        '''Globally unique artifact identifier.  Used for
        SOLR ID, shortlinks, and maybe elsewhere
        '''
        id = '%s.%s#%s' % (
            self.__class__.__module__,
            self.__class__.__name__,
            self._id)
        return id.replace('.', '/')

    @classmethod
    def upsert(cls, id, **kwargs):
        isnew = False
        r = cls.query.get(_id=id)
        if r is not None: return r, isnew
        try:
            r = cls(_id=id, **kwargs)
            session(r).flush(r)
            isnew = True
        except pymongo.errors.DuplicateKeyError: # pragma no cover
            session(r).expunge(r)
            r = cls.query.get(_id=id)
        return r, isnew

class Commit(RepoObject):
    type_s = 'Commit'
    # Ephemeral attrs
    repo=None

    def set_context(self, repo):
        self.repo = repo

    @LazyProperty
    def author_url(self):
        u = User.by_email_address(self.authored.email)
        if u: return u.url()

    @LazyProperty
    def committer_url(self):
        u = User.by_email_address(self.committed.email)
        if u: return u.url()

    @LazyProperty
    def tree(self):
        if self.tree_id is None:
            self.tree_id = self.repo.compute_tree_new(self)
        if self.tree_id is None:
            return None
        cache = getattr(c, 'model_cache', '') or ModelCache()
        t = cache.get(Tree, dict(_id=self.tree_id))
        if t is None:
            self.tree_id = self.repo.compute_tree_new(self)
            t = Tree.query.get(_id=self.tree_id)
        if t is not None: t.set_context(self)
        return t

    @LazyProperty
    def summary(self):
        message = h.really_unicode(self.message)
        first_line = message.split('\n')[0]
        return h.text.truncate(first_line, 50)

    def shorthand_id(self):
        if self.repo is None: self.repo = self.guess_repo()
        if self.repo is None: return repr(self)
        return self.repo.shorthand_for_commit(self._id)

    @LazyProperty
    def symbolic_ids(self):
        return self.repo.symbolics_for_commit(self)

    def get_parent(self, index=0):
        '''Get the parent of this commit.

        If there is no parent commit, or if an invalid index is given,
        returns None.
        '''
        try:
            cache = getattr(c, 'model_cache', '') or ModelCache()
            ci = cache.get(Commit, dict(_id=self.parent_ids[index]))
            ci.set_context(self.repo)
            return ci
        except IndexError as e:
            return None

    def climb_commit_tree(self):
        '''
        Returns a generator that walks up the commit tree along
        the first-parent ancestory, starting with this commit.'''
        yield self
        ancestor = self.get_parent()
        while ancestor:
            yield ancestor
            ancestor = ancestor.get_parent()

    def url(self):
        if self.repo is None: self.repo = self.guess_repo()
        if self.repo is None: return '#'
        return self.repo.url_for_commit(self)

    def guess_repo(self):
        for ac in c.project.app_configs:
            try:
                app = c.project.app_instance(ac)
                if app.repo._id in self.repo_ids:
                    return app.repo
            except AttributeError:
                pass
        return None

    def link_text(self):
        '''The link text that will be used when a shortlink to this artifact
        is expanded into an <a></a> tag.

        By default this method returns shorthand_id(). Subclasses should
        override this method to provide more descriptive link text.
        '''
        return self.shorthand_id()

    def context(self):
        result = dict(prev=None, next=None)
        if self.parent_ids:
            result['prev'] = self.query.find(dict(_id={'$in': self.parent_ids })).all()
            for ci in result['prev']:
                ci.set_context(self.repo)
        if self.child_ids:
            result['next'] = self.query.find(dict(_id={'$in': self.child_ids })).all()
            for ci in result['next']:
                ci.set_context(self.repo)
        return result

    @LazyProperty
    def diffs(self):
        di = DiffInfoDoc.m.get(_id=self._id)
        if di is None:
            return Object(added=[], removed=[], changed=[], copied=[])
        added = []
        removed = []
        changed = []
        copied = []
        for change in di.differences:
            if change.rhs_id is None:
                removed.append(change.name)
            elif change.lhs_id is None:
                added.append(change.name)
            else:
                changed.append(change.name)
        copied = self._diffs_copied(added, removed)
        return Object(
            added=added, removed=removed,
            changed=changed, copied=copied)

    def _diffs_copied(self, added, removed):
        '''Return list with file renames diffs.

        Will change `added` and `removed` lists also.
        '''
        def _blobs_similarity(removed_blob, added):
            best = dict(ratio=0, name='', blob=None)
            for added_name in added:
                added_blob = self.tree.get_obj_by_path(added_name)
                if not isinstance(added_blob, Blob):
                    continue
                diff = SequenceMatcher(None, removed_blob.text,
                                       added_blob.text)
                ratio = diff.quick_ratio()
                if ratio > best['ratio']:
                    best['ratio'] = ratio
                    best['name'] = added_name
                    best['blob'] = added_blob

                if ratio == 1:
                    break  # we'll won't find better similarity than 100% :)

            if best['ratio'] > DIFF_SIMILARITY_THRESHOLD:
                diff = ''
                if best['ratio'] < 1:
                    added_blob = best['blob']
                    rpath = ('a' + removed_blob.path()).encode('utf-8')
                    apath = ('b' + added_blob.path()).encode('utf-8')
                    diff = ''.join(unified_diff(list(removed_blob),
                                                list(added_blob),
                                                rpath, apath))
                return dict(new=best['name'],
                            ratio=best['ratio'], diff=diff)

        def _trees_similarity(removed_tree, added):
            for added_name in added:
                added_tree = self.tree.get_obj_by_path(added_name)
                if not isinstance(added_tree, Tree):
                    continue
                if removed_tree._id == added_tree._id:
                    return dict(new=added_name,
                                ratio=1, diff='')

        if not removed:
            return []
        copied = []
        prev_commit = self.get_parent()
        for removed_name in removed[:]:
            removed_blob = prev_commit.tree.get_obj_by_path(removed_name)
            rename_info = None
            if isinstance(removed_blob, Blob):
                rename_info = _blobs_similarity(removed_blob, added)
            elif isinstance(removed_blob, Tree):
                rename_info = _trees_similarity(removed_blob, added)
            if rename_info is not None:
                rename_info['old'] = removed_name
                copied.append(rename_info)
                removed.remove(rename_info['old'])
                added.remove(rename_info['new'])
        return copied

    def get_path(self, path):
        if path[0] == '/': path = path[1:]
        parts = path.split('/')
        cur = self.tree
        for part in parts:
            cur = cur[part]
        return cur

    @LazyProperty
    def changed_paths(self):
        '''
        Returns a list of paths changed in this commit.
        Leading and trailing slashes are removed, and
        the list is complete, meaning that if a sub-path
        is changed, all of the parent paths are included
        (including '' to represent the root path).

        Example:

            If the file /foo/bar is changed in the commit,
            this would return ['', 'foo', 'foo/bar']
        '''
        diff_info = DiffInfoDoc.m.get(_id=self._id)
        diffs = set()
        for d in diff_info.differences:
            diffs.add(d.name.strip('/'))
            node_path = os.path.dirname(d.name)
            while node_path:
                diffs.add(node_path)
                node_path = os.path.dirname(node_path)
            diffs.add('')  # include '/' if there are any changes
        return diffs

    @LazyProperty
    def info(self):
        return dict(
            id=self._id,
            author=self.authored.name,
            author_email=self.authored.email,
            date=self.authored.date,
            author_url=self.author_url,
            shortlink=self.shorthand_id(),
            summary=self.summary
            )

class Tree(RepoObject):
    # Ephemeral attrs
    repo=None
    commit=None
    parent=None
    name=None

    def compute_hash(self):
        '''Compute a hash based on the contents of the tree.  Note that this
        hash does not necessarily correspond to any actual DVCS hash.
        '''
        lines = (
            [ 'tree' + x.name + x.id for x in self.tree_ids ]
            + [ 'blob' + x.name + x.id for x in self.blob_ids ]
            + [ x.type + x.name + x.id for x in self.other_ids ])
        sha_obj = sha1()
        for line in sorted(lines):
            sha_obj.update(line)
        return sha_obj.hexdigest()

    def __getitem__(self, name):
        cache = getattr(c, 'model_cache', '') or ModelCache()
        obj = self.by_name[name]
        if obj['type'] == 'blob':
            return Blob(self, name, obj['id'])
        obj = cache.get(Tree, dict(_id=obj['id']))
        if obj is None:
            oid = self.repo.compute_tree_new(self.commit, self.path() + name + '/')
            obj = cache.get(Tree, dict(_id=oid))
        if obj is None: raise KeyError, name
        obj.set_context(self, name)
        return obj

    def get_obj_by_path(self, path):
        if hasattr(path, 'get'):
            path = path['new']
        if path.startswith('/'):
            path = path[1:]
        path = path.split('/')
        obj = self
        for p in path:
            try:
                obj = obj[p]
            except KeyError:
                return None
        return obj

    def get_blob_by_path(self, path):
        obj = self.get_obj_by_path(path)
        return obj if isinstance(obj, Blob) else None

    def set_context(self, commit_or_tree, name=None):
        assert commit_or_tree is not self
        self.repo = commit_or_tree.repo
        if name:
            self.commit = commit_or_tree.commit
            self.parent = commit_or_tree
            self.name = name
        else:
            self.commit = commit_or_tree

    def readme(self):
        'returns (filename, unicode text) if a readme file is found'
        for x in self.blob_ids:
            if README_RE.match(x.name):
                name = x.name
                blob = self[name]
                return (x.name, h.really_unicode(blob.text))
        return None, None

    def ls(self):
        '''
        List the entries in this tree, with historical commit info for
        each node.  Eventually, ls_old can be removed and this can be
        replaced with the following:

            last_commit = LastCommit.get(self)
            return sorted(last_commit.entries, cmp=lambda a,b: cmp(b.type,a.type) or cmp(a.name,b.name))
        '''
        # look for existing new format first
        last_commit = LastCommit.query.get(
                commit_ids=self.commit._id,
                path=self.path().strip('/'),
            )
        if last_commit:
            sorted_entries = sorted(last_commit.entries, cmp=lambda a,b: cmp(b.type,a.type) or cmp(a.name,b.name))
            mapped_entries = [self._dirent_map(e) for e in sorted_entries]
            return mapped_entries
        # otherwise, try old format
        old_style_results = self.ls_old()
        if old_style_results:
            return old_style_results
        # finally, use the new implentation that auto-vivifies
        last_commit = LastCommit.get(self)
        sorted_entries = sorted(last_commit.entries, cmp=lambda a,b: cmp(b.type,a.type) or cmp(a.name,b.name))
        mapped_entries = [self._dirent_map(e) for e in sorted_entries]
        return mapped_entries

    def _dirent_map(self, dirent):
        return dict(
                kind=dirent.type,
                name=dirent.name,
                href=dirent.name,
                last_commit=dict(
                        author=dirent.commit_info.author,
                        author_email=dirent.commit_info.author_email,
                        author_url=dirent.commit_info.author_url,
                        date=dirent.commit_info.date,
                        href=self.repo.url_for_commit(dirent.commit_info['id']),
                        shortlink=dirent.commit_info.shortlink,
                        summary=dirent.commit_info.summary,
                    ),
            )

    def ls_old(self):
        # Load last commit info
        id_re = re.compile("^{0}:{1}:".format(
            self.repo._id,
            re.escape(h.really_unicode(self.path()).encode('utf-8'))))
        lc_index = dict(
            (lc.name, lc.commit_info)
            for lc in LastCommitDoc_old.m.find(dict(_id=id_re)))

        # FIXME: Temporarily fall back to old, semi-broken lookup behavior until refresh is done
        oids = [ x.id for x in chain(self.tree_ids, self.blob_ids, self.other_ids) ]
        id_re = re.compile("^{0}:".format(self.repo._id))
        lc_index.update(dict(
            (lc.object_id, lc.commit_info)
            for lc in LastCommitDoc_old.m.find(dict(_id=id_re, object_id={'$in': oids}))))
        # /FIXME

        if not lc_index:
            # allow fallback to new method instead
            # of showing a bunch of Nones
            return []

        results = []
        def _get_last_commit(name, oid):
            lc = lc_index.get(name, lc_index.get(oid, None))
            if lc is None:
                lc = dict(
                    author=None,
                    author_email=None,
                    author_url=None,
                    date=None,
                    id=None,
                    href=None,
                    shortlink=None,
                    summary=None)
            if 'href' not in lc:
                lc['href'] = self.repo.url_for_commit(lc['id'])
            return lc
        for x in sorted(self.tree_ids, key=lambda x:x.name):
            results.append(dict(
                    kind='DIR',
                    name=x.name,
                    href=x.name + '/',
                    last_commit=_get_last_commit(x.name, x.id)))
        for x in sorted(self.blob_ids, key=lambda x:x.name):
            results.append(dict(
                    kind='FILE',
                    name=x.name,
                    href=x.name,
                    last_commit=_get_last_commit(x.name, x.id)))
        for x in sorted(self.other_ids, key=lambda x:x.name):
            results.append(dict(
                    kind=x.type,
                    name=x.name,
                    href=None,
                    last_commit=_get_last_commit(x.name, x.id)))
        return results

    def path(self):
        if self.parent:
            assert self.parent is not self
            return self.parent.path() + self.name + '/'
        else:
            return '/'

    def url(self):
        return self.commit.url() + 'tree' + self.path()

    @LazyProperty
    def by_name(self):
        d = Object((x.name, x) for x in self.other_ids)
        d.update(
            (x.name, Object(x, type='tree'))
            for x in self.tree_ids)
        d.update(
            (x.name, Object(x, type='blob'))
            for x in self.blob_ids)
        return d

    def is_blob(self, name):
        return self.by_name[name]['type'] == 'blob'

    def get_blob(self, name):
        x = self.by_name[name]
        return Blob(self, name, x.id)

class Blob(object):
    '''Lightweight object representing a file in the repo'''

    def __init__(self, tree, name, _id):
        self._id = _id
        self.tree = tree
        self.name = name
        self.repo = tree.repo
        self.commit = tree.commit
        fn, ext = os.path.splitext(self.name)
        self.extension = ext or fn

    def path(self):
        return self.tree.path() + h.really_unicode(self.name)

    def url(self):
        return self.tree.url() + h.really_unicode(self.name)

    @LazyProperty
    def prev_commit(self):
        lc = LastCommit.get(self.tree)
        if lc:
            entry = lc.entry_by_name(self.name)
            last_commit = self.repo.commit(entry.commit_info.id)
            prev_commit = last_commit.get_parent()
            try:
                tree = prev_commit and prev_commit.get_path(self.tree.path().rstrip('/'))
            except KeyError:
                return None
            lc = tree and LastCommit.get(tree)
            entry = lc and lc.entry_by_name(self.name)
            if entry:
                prev_commit = self.repo.commit(entry.commit_info.id)
                return prev_commit
        return None

    @LazyProperty
    def next_commit(self):
        try:
            path = self.path()
            cur = self.commit
            next = cur.context()['next']
            while next:
                cur = next[0]
                next = cur.context()['next']
                other_blob = cur.get_path(path)
                if other_blob is None or other_blob._id != self._id:
                    return cur
        except:
            log.exception('Lookup prev_commit')
            return None

    @LazyProperty
    def _content_type_encoding(self):
        return self.repo.guess_type(self.name)

    @LazyProperty
    def content_type(self):
        return self._content_type_encoding[0]

    @LazyProperty
    def content_encoding(self):
        return self._content_type_encoding[1]

    @property
    def has_pypeline_view(self):
        if README_RE.match(self.name) or self.extension in ['.md', '.rst']:
            return True
        return False

    @property
    def has_html_view(self):
        if (self.content_type.startswith('text/') or
            self.extension in VIEWABLE_EXTENSIONS or
            self.extension in self.repo._additional_viewable_extensions or
            utils.is_text_file(self.text)):
            return True
        return False

    @property
    def has_image_view(self):
        return self.content_type.startswith('image/')

    def context(self):
        path = self.path()
        prev = self.prev_commit
        next = self.next_commit
        if prev is not None: prev = prev.get_path(path)
        if next is not None: next = next.get_path(path)
        return dict(
            prev=prev,
            next=next)

    def open(self):
        return self.repo.open_blob(self)

    def __iter__(self):
        return iter(self.open())

    @LazyProperty
    def size(self):
        return self.repo.blob_size(self)

    @LazyProperty
    def text(self):
        return self.open().read()

    @classmethod
    def diff(cls, v0, v1):
        differ = SequenceMatcher(v0, v1)
        return differ.get_opcodes()

class LastCommit(RepoObject):
    def __repr__(self):
        return '<LastCommit /%s [%s]>' % (self.path, ',\n    '.join(self.commit_ids))

    @classmethod
    def get(cls, tree):
        '''Find the LastCommitDoc for the given tree.

        Climbs the commit tree until either:

        1) An LCD is found for the given tree.  (If the LCD was not found for the
           tree's commit, the commits traversed while searching for it are
           added to the LCD for faster retrieval in the future.)

        2) The commit in which the tree was most recently modified is found.
           In this case, we know that the LCD hasn't been constructed for this
           (chain of) commit(s), and it will have to be built.
        '''
        cache = getattr(c, 'model_cache', '') or ModelCache()
        path = tree.path().strip('/')
        commit_ids = []
        cache._get_calls += 1
        gw = 0
        for commit in tree.commit.climb_commit_tree():
            last_commit = cache.get(LastCommit, dict(
                    commit_ids=commit._id,
                    path=path,
                ))
            if last_commit:
                cache._get_hits += 1
                # found our LCD; add any traversed commits to it
                if commit_ids:
                    last_commit.commit_ids.extend(commit_ids)
                    for commit_id in commit_ids:
                        cache.set(LastCommit, dict(commit_ids=commit_id, path=path), last_commit)
                return last_commit
            commit_ids.append(commit._id)
            if path in commit.changed_paths:
                cache._get_misses += 1
                # tree was changed but no LCD found; have to build
                tree = commit.tree
                if path != '':
                    tree = tree.get_obj_by_path(path)
                return cls.build(tree, commit_ids)
            cache._get_walks += 1
            gw += 1
            cache._get_walks_max = max(cache._get_walks_max, gw)

    @classmethod
    def build(cls, tree, commit_ids=[]):
        '''
          Build the LCD record, presuming that this tree is where it was most
          recently changed.

          To build the LCD, we climb the commit tree, keeping track of which
          entries we still need info about.  (For multi-parent commits, it
          doesn't matter which parent we follow because those would be merge
          commits and ought to have the diff info populated for any file
          touched by the merge.)  At each step of the walk, we check the following:

            1) If the current tree has an LCD record, we can pull all the remaining
               info we need from it, and we're done.

            2) If the tree was modified in this commit, then we pull the info for
               all changed entries, then continue up the tree.  Once we have data
               for all entries, we're done.

          (It may be possible to optimize this for SVN, if SVN can return all of
          the LCD info from a single call and if that turns out to be more efficient
          than walking up the tree.  It is unclear if those hold without testing.)
        '''
        cache = getattr(c, 'model_cache', '') or ModelCache()
        unfilled = set([n.name for n in chain(tree.tree_ids, tree.blob_ids, tree.other_ids)])
        tree_nodes = set([n.name for n in tree.tree_ids])
        path = tree.path().strip('/')
        lcd = cls(
                    commit_ids=commit_ids,
                    path=path,
                    entries=[],
                )
        cache._build_calls += 1
        bw = 0
        for commit in tree.commit.climb_commit_tree():
            partial_lcd = cache.get(LastCommit, dict(
                    commit_ids=commit._id,
                    path=path,
                ))
            for name in list(unfilled):
                if os.path.join(path, name) in commit.changed_paths:
                    # changed in this commit, so gather the data
                    lcd.entries.append(dict(
                            type=name in tree_nodes and 'DIR' or 'BLOB',
                            name=name,
                            commit_info=commit.info,
                        ))
                    unfilled.remove(name)
                elif partial_lcd:
                    # the partial LCD should contain anything we're missing
                    entry = partial_lcd.entry_by_name(name)
                    assert entry
                    lcd.entries.append(entry)
                    unfilled.remove(name)

            if not unfilled:
                break
            cache._build_walks += 1
            bw += 1
            cache._build_walks_max = max(cache._build_walks_max, bw)
        for commit_id in commit_ids:
            cache.set(LastCommit, dict(commit_ids=commit_id, path=path), lcd)
        return lcd

    def entry_by_name(self, name):
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

mapper(Commit, CommitDoc, repository_orm_session)
mapper(Tree, TreeDoc, repository_orm_session)
mapper(LastCommit, LastCommitDoc, repository_orm_session)


class ModelCache(object):
    '''
    Cache model instances based on query params passed to get.
    '''
    def __init__(self, max_size=2000):
        '''
        The max_size of the cache is tracked separately for
        each model class stored.  I.e., you can have 2000
        Commit instances and 2000 Tree instances in the cache
        at once with the default value.
        '''
        self._cache = defaultdict(OrderedDict)
        self.max_size = max_size
        # temporary, for performance testing
        self._hits = defaultdict(int)
        self._accesses = defaultdict(int)
        self._get_calls = 0
        self._get_walks = 0
        self._get_walks_max = 0
        self._get_hits = 0
        self._get_misses = 0
        self._build_calls = 0
        self._build_walks = 0
        self._build_walks_max = 0

    def _normalize_key(self, key):
        _key = key
        if not isinstance(_key, tuple):
            _key = tuple(sorted(_key.items(), key=lambda k: k[0]))
        return _key

    def get(self, cls, key):
        _key = self._normalize_key(key)
        self._manage_cache(cls, _key)
        self._accesses[cls] += 1
        if _key not in self._cache[cls]:
            query = getattr(cls, 'query', getattr(cls, 'm', None))
            self.set(cls, _key, query.get(**key))
        else:
            self._hits[cls] += 1
        return self._cache[cls][_key]

    def set(self, cls, key, val):
        _key = self._normalize_key(key)
        self._manage_cache(cls, _key)
        self._cache[cls][_key] = val

    def _manage_cache(self, cls, key):
        '''
        Keep track of insertion order, prevent duplicates,
        and expire from the cache in a FIFO manner.
        '''
        if key in self._cache[cls]:
            # refresh access time in cache
            val = self._cache[cls].pop(key)
            self._cache[cls][key] = val
        elif len(self._cache[cls]) >= self.max_size:
            # remove the least-recently-used cache item
            self._cache[cls].popitem(last=False)

    def size(self):
        return sum([len(c) for c in self._cache.values()])

    def keys(self, cls, as_dict=True):
        '''
        Returns all the cache keys for a given class.  Each
        cache key will be a dict.
        '''
        if as_dict:
            return [dict(k) for k in self._cache[cls].keys()]
        else:
            return self._cache[cls].keys()

    def batch_load(self, cls, query, attrs=None):
        '''
        Load multiple results given a query.

        Optionally takes a list of attribute names to use
        as the cache key.  If not given, uses the keys of
        the given query.
        '''
        if attrs is None:
            attrs = query.keys()
        for result in cls.query.find(query):
            keys = {a: getattr(result, a) for a in attrs}
            self.set(cls, keys, result)
