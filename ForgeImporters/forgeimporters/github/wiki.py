#       Licensed to the Apache Software Foundation (ASF) under one
#       or more contributor license agreements.  See the NOTICE file
#       distributed with this work for additional information
#       regarding copyright ownership.  The ASF licenses this file
#       to you under the Apache License, Version 2.0 (the
#       "License"); you may not use this file except in compliance
#       with the License.  You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#       Unless required by applicable law or agreed to in writing,
#       software distributed under the License is distributed on an
#       "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#       KIND, either express or implied.  See the License for the
#       specific language governing permissions and limitations
#       under the License.

import os
import re
from datetime import datetime
from tempfile import mkdtemp
from shutil import rmtree

from BeautifulSoup import BeautifulSoup
import git
from pylons import app_globals as g
from pylons import tmpl_context as c
from ming.orm import ThreadLocalORMSession
from formencode import validators as fev
from tg import (
        expose,
        validate,
        flash,
        redirect,
        )
from tg.decorators import (
        with_trailing_slash,
        without_trailing_slash,
        )

from allura.controllers import BaseController
from allura.lib import helpers as h
from allura.lib.plugin import ImportIdConverter
from allura.lib.decorators import (
        require_post,
        )
from allura import model as M
from forgeimporters.base import (
        ToolImporter,
        ToolImportForm,
        )
from forgeimporters.github import GitHubProjectExtractor
from forgewiki import model as WM
from forgewiki.converters import mediawiki2markdown


import logging
log = logging.getLogger(__name__)

TARGET_APPS = []

try:
    from forgewiki.wiki_main import ForgeWikiApp
    TARGET_APPS.append(ForgeWikiApp)
except ImportError:
    pass


class GitHubWikiImportForm(ToolImportForm):
    gh_project_name = fev.UnicodeString(not_empty=True)
    gh_user_name = fev.UnicodeString(not_empty=True)
    tool_option = fev.UnicodeString(if_missing=u'')


class GitHubWikiImportController(BaseController):

    def __init__(self):
        self.importer = GitHubWikiImporter()

    @property
    def target_app(self):
        return self.importer.target_app[0]

    @with_trailing_slash
    @expose('jinja:forgeimporters.github:templates/wiki/index.html')
    def index(self, **kw):
        return dict(importer=self.importer,
                    target_app=self.target_app)

    @without_trailing_slash
    @expose()
    @require_post()
    @validate(GitHubWikiImportForm(ForgeWikiApp), error_handler=index)
    def create(self, gh_project_name, gh_user_name, mount_point, mount_label, **kw):
        if self.importer.enforce_limit(c.project):
            self.importer.post(
                project_name=gh_project_name,
                user_name=gh_user_name,
                mount_point=mount_point,
                mount_label=mount_label,
                tool_option=kw.get('tool_option'))
            flash('Wiki import has begun. Your new wiki will be available '
                  'when the import is complete.')
        else:
            flash('There are too many imports pending at this time.  Please wait and try again.', 'error')
        redirect(c.project.url() + 'admin/')


class GitHubWikiImporter(ToolImporter):
    target_app = TARGET_APPS
    controller = GitHubWikiImportController
    source = 'GitHub'
    tool_label = 'Wiki'
    tool_description = 'Import your wiki from GitHub'
    tool_option = {"import_history": "Import history"}

    mediawiki_exts = ['.wiki', '.mediawiki']
    markdown_exts = ['.markdown,' '.mdown', '.mkdn', '.mkd', '.md']
    # List of supported formats https://github.com/gollum/gollum/wiki#page-files
    supported_formats = [
            '.asciidoc',
            '.creole',
            '.org',
            '.pod',
            '.rdoc',
            '.rest.txt',
            '.rst.txt',
            '.rest',
            '.rst',
            '.textile',
    ] + mediawiki_exts + markdown_exts
    available_pages = []

    def import_tool(self, project, user, project_name=None, mount_point=None, mount_label=None, user_name=None,
                    tool_option=None, **kw):
        """ Import a GitHub wiki into a new Wiki Allura tool.

        """
        project_name = "%s/%s" % (user_name, project_name)
        extractor = GitHubProjectExtractor(project_name)
        if not extractor.has_wiki():
            return

        self.github_wiki_url = extractor.get_page_url('wiki_url').replace('.wiki', '/wiki')
        self.app = project.install_app(
            "Wiki",
            mount_point=mount_point or 'wiki',
            mount_label=mount_label or 'Wiki',
            import_id={
                'source': self.source,
                'project_name': project_name,
            }
        )
        with_history = tool_option == 'import_history'
        ThreadLocalORMSession.flush_all()
        try:
            M.session.artifact_orm_session._get().skip_mod_date = True
            with h.push_config(c, app=self.app):
                self.import_pages(extractor.get_page_url('wiki_url'), history=with_history)
            ThreadLocalORMSession.flush_all()
            M.AuditLog.log(
                'import tool %s from %s on %s' % (
                    self.app.config.options.mount_point,
                    project_name,
                    self.source),
                project=project,
                user=user,
                url=self.app.url)
            g.post_event('project_updated')
            return self.app
        except Exception as e:
            h.make_app_admin_only(self.app)
            raise
        finally:
            M.session.artifact_orm_session._get().skip_mod_date = False

    def _set_available_pages(self, commit):
        pages = [blob.name for blob in commit.tree.traverse()]
        pages = map(os.path.splitext, pages)
        pages = [self._convert_page_name(name) for name, ext in pages
                if ext in self.supported_formats]
        self.available_pages = pages

    def _without_history(self, commit):
        self._set_available_pages(commit)
        for page in commit.tree.blobs:
            self._make_page(page.data_stream.read(), page.name, commit)

    def _with_history(self, commit):
        for filename in commit.stats.files.keys():
            self._set_available_pages(commit)
            if filename in commit.tree:
                text = commit.tree[filename].data_stream.read()
            else:
                # file is deleted
                text = ''
            self._make_page(text, filename, commit)

    def _make_page(self, text, filename, commit):
        orig_name, ext = os.path.splitext(filename)
        if ext and ext not in self.supported_formats:
            log.info('Not a wiki page %s. Skipping.' % filename)
            return
        mod_date = datetime.utcfromtimestamp(commit.committed_date)
        name = self._convert_page_name(orig_name)
        wiki_page = WM.Page.upsert(name)
        if filename in commit.tree:
            wiki_page.text = self.convert_markup(h.really_unicode(text), filename)
            wiki_page.timestamp = wiki_page.mod_date = mod_date
            wiki_page.viewable_by = ['all']
        else:
            wiki_page.delete()
        wiki_page.import_id = ImportIdConverter.get().expand(orig_name, self.app)
        wiki_page.commit()
        return wiki_page

    def _convert_page_name(self, name):
        """Convert '-' and '/' into spaces in page name to match github behavior"""
        return name.replace('-', ' ').replace('/', ' ')

    def import_pages(self, wiki_url, history=None):
        wiki_path = mkdtemp()
        wiki = git.Repo.clone_from(wiki_url, to_path=wiki_path, bare=True)
        if not history:
            self._without_history(wiki.heads.master.commit)
        else:
            for commit in reversed(list(wiki.iter_commits())):
                self._with_history(commit)
        rmtree(wiki_path)

    def convert_markup(self, text, filename):
        """Convert any supported github markup into Allura-markdown.

        Conversion happens in 4 phases:

        1. Convert source text to a html using h.render_any_markup.
        2. Rewrite links that match the wiki URL prefix with new location.
        3. Convert resulting html to a markdown using html2text, if available.
        4. Convert gollum tags

        If html2text module isn't available then only phases 1 and 2 will be executed.

        Files in mediawiki format are converted using mediawiki2markdown
        if html2text is available.
        """
        name, ext = os.path.splitext(filename)
        if ext in self.markdown_exts:
            return text

        try:
            import html2text
            html2text.BODY_WIDTH = 0
        except ImportError:
            html2text = None

        if ext and ext in self.mediawiki_exts:
            if html2text:
                text = mediawiki2markdown(text)
                text = self.convert_gollum_tags(text)
                # Don't have html here, so we can't call self._rewrite_links.
                # Falling back to simpler rewriter.
                prefix = self.github_wiki_url
                new_prefix = self.app.url
                if not prefix.endswith('/'):
                    prefix += '/'
                if not new_prefix.endswith('/'):
                    new_prefix += '/'
                _re = re.compile(r'%s(\S*)' % prefix)
                def repl(m):
                    return new_prefix + self._convert_page_name(m.group(1))
                text = _re.sub(repl, text)
            else:
                text = h.render_any_markup(filename, text)
                text = self.rewrite_links(text, self.github_wiki_url, self.app.url)
            return text
        else:
            text = h.render_any_markup(filename, text)
            text = self.rewrite_links(text, self.github_wiki_url, self.app.url)
            if html2text:
                text = html2text.html2text(text)
                text = self.convert_gollum_tags(text)
            return text

    def convert_gollum_tags(self, text):
        tag_re = re.compile(r'''
            (?P<quote>')?             # optional tag escaping
            (?P<tag>\[\[              # tag start
            (?P<link>[^]]+)           # title/link/filename with options
            \]\])                     # tag end
        ''', re.VERBOSE)
        return tag_re.sub(self._gollum_tag_match, text)

    def _gollum_tag_match(self, match):
        available_options = [
            'alt=',
            'frame',
            'align=',
            'float',
            'width=',
            'height=',
        ]
        quote = match.groupdict().get('quote')
        if quote:
            # tag is escaped, return untouched
            return match.group('tag')
        link = match.group('link').split('|')
        title = options = None
        if len(link) == 1:
            link = link[0]
        elif any(map(lambda opt: link[1].startswith(opt), available_options)):
            # second element is option -> first is the link
            link, options = link[0], link[1:]
        else:
            title, link, options = link[0], link[1], link[2:]

        if link == '_TOC_':
            return '[TOC]'

        if link.startswith('http://') or link.startswith('https://'):
            sub = self._gollum_external_link
        # TODO: add embedded images and file links
        else:
            sub = self._gollum_page_link
        return sub(link, title, options)

    def _gollum_external_link(self, link, title, options):
        if title:
            return u'[{}]({})'.format(title, link)
        return u'<{}>'.format(link)

    def _gollum_page_link(self, link, title, options):
        page = self._convert_page_name(link)
        page = page.replace(u'&amp;', u'&')  # allow & in page links
        # gollum page lookups are case-insensitive, you'll always get link to
        # whatever comes first in the file system, no matter how you refer to a page.
        # E.g. if you have two pages: a.md and A.md both [[a]] and [[A]] will refer a.md.
        # We're emulating this behavior using list of all available pages
        try:
            idx = map(lambda p: p.lower(), self.available_pages).index(page.lower())
        except ValueError:
            idx = None
        if idx is not None:
            page = self.available_pages[idx]

        if title:
            return u'[{}]({})'.format(title, page)
        return u'[{}]'.format(page)

    def rewrite_links(self, html, prefix, new_prefix):
        if not prefix.endswith('/'):
            prefix += '/'
        if not new_prefix.endswith('/'):
            new_prefix += '/'
        soup = BeautifulSoup(html)
        for a in soup.findAll('a'):
            if a.get('href').startswith(prefix):
                page = a['href'].replace(prefix, '')
                new_page = self._convert_page_name(page)
                a['href'] = new_prefix + new_page
                if a.text == page:
                    a.setString(new_page)
                elif a.text == prefix + page:
                    a.setString(new_prefix + new_page)
        return unicode(soup)