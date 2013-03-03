import pymongo
from pylons import tmpl_context as c, app_globals as g
from pylons import request

import bson
from ming import schema as S
from ming import Field, Index, collection
from ming.orm import session, state, Mapper
from ming.orm import FieldProperty
from ming.orm.declarative import MappedClass
from datetime import datetime, timedelta
import difflib

from allura.model.session import main_orm_session
from allura.lib import helpers as h

class Stats(MappedClass):
    class __mongometa__:
        name='stats'
        session = main_orm_session
        unique_indexes = [ '_id']

    _id=FieldProperty(S.ObjectId)

    visible = FieldProperty(bool, if_missing = True)
    registration_date = FieldProperty(datetime)
    general = FieldProperty([dict(
        category = S.ObjectId,
        messages = [dict(
            messagetype = str,
            created = int,
            modified = int)],
        tickets = dict(
            solved = int,
            assigned = int,
            revoked = int,
            totsolvingtime = int),
        commits = [dict(
            lines = int,
            number = int,
            language = S.ObjectId)])])

    lastmonth=FieldProperty(dict(
        messages=[dict(
            datetime=datetime,
            created=bool,
            categories=[S.ObjectId],
            messagetype=str)],
        assignedtickets=[dict(
            datetime=datetime,
            categories=[S.ObjectId])],
        revokedtickets=[dict(
            datetime=datetime,
            categories=[S.ObjectId])],
        solvedtickets=[dict(
            datetime=datetime,
            categories=[S.ObjectId],
            solvingtime=int)],
        commits=[dict(
            datetime=datetime,
            categories=[S.ObjectId],
            programming_languages=[S.ObjectId],
            lines=int)]))

    def getCodeContribution(self):
        days=(datetime.today() - self.registration_date).days
        if not days:
            days=1
        for val in self['general']:
            if val['category'] is None:
                for commits in val['commits']:
                    if commits['language'] is None: 
                        if days > 30:
                            return round(float(commits.lines)/days*30, 2)
                        else:
                            return float(commits.lines)
        return 0

    def getDiscussionContribution(self):
        days=(datetime.today() - self.registration_date).days
        if not days:
            days=1
        for val in self['general']:
            if val['category'] is None:
                for artifact in val['messages']:
                    if artifact['messagetype'] is None: 
                        tot = artifact.created+artifact.modified
                        if days > 30:
                            return round(float(tot)/days*30,2)
                        else:
                            return float(tot)
        return 0

    def getTicketsContribution(self):
        for val in self['general']:
            if val['category'] is None:
                tickets = val['tickets']
                if tickets.assigned == 0:
                    return 0
                return float(tickets.solved) / tickets.assigned
        return 0

    @classmethod
    def getMaxAndAverageCodeContribution(self):
        lst = list(self.query.find())
        n = len(lst)
        if n == 0:
            return 0, 0
        maxcontribution=max([x.getCodeContribution() for x in lst])
        averagecontribution=sum([x.getCodeContribution() for x in lst]) / n
        return maxcontribution, round(averagecontribution, 2)

    @classmethod
    def getMaxAndAverageDiscussionContribution(self):
        lst = list(self.query.find())
        n = len(lst)
        if n == 0:
            return 0, 0
        maxcontribution=max([x.getDiscussionContribution() for x in lst])
        averagecontribution=sum([x.getDiscussionContribution() for x in lst])/n
        return maxcontribution, round(averagecontribution, 2)

    @classmethod
    def getMaxAndAverageTicketsSolvingPercentage(self):
        lst = list(self.query.find())
        n = len(lst)
        if n == 0:
            return 0, 0
        maxcontribution=max([x.getTicketsContribution() for x in lst])
        averagecontribution=sum([x.getTicketsContribution() for x in lst])/n
        return maxcontribution, round(averagecontribution, 2)

    def codeRanking(self):
        lst = list(self.query.find())
        totn = len(lst)
        codcontr = self.getCodeContribution()
        upper = len([x for x in lst if x.getCodeContribution() > codcontr])
        return round((totn - upper) * 100.0 / totn, 2)

    def discussionRanking(self):
        lst = list(self.query.find())
        totn = len(lst)
        disccontr = self.getDiscussionContribution()
        upper=len([x for x in lst if x.getDiscussionContribution()>disccontr])
        return round((totn - upper) * 100.0 / totn, 2)

    def ticketsRanking(self):
        lst = list(self.query.find())
        totn = len(lst)
        ticketscontr = self.getTicketsContribution()
        upper=len([x for x in lst if x.getTicketsContribution()>ticketscontr])
        return round((totn - upper) * 100.0 / totn, 2)

    def getCommits(self, category = None):
        i = getElementIndex(self.general, category = category)
        if i is None: 
            return dict(number=0, lines=0)
        cat = self.general[i]
        j = getElementIndex(cat.commits, language = None)
        if j is None:
            return dict(number=0, lines=0)
        return dict(
            number=cat.commits[j]['number'], 
            lines=cat.commits[j]['lines'])

    def getArtifacts(self, category = None, art_type = None):
        i = getElementIndex(self.general, category = category)
        if i is None:
            return dict(created=0, modified=0)
        cat = self.general[i]
        j = getElementIndex(cat.messages, messagetype = art_type)
        if j is None:
            return dict(created=0, modified=0)
        return dict(created=cat.messages[j].created, modified=cat.messages[j].modified)

    def getTickets(self, category = None):
        i = getElementIndex(self.general, category = category)
        if i is None:
            return dict(
                assigned=0,
                solved=0,
                revoked=0,
                averagesolvingtime=None)
        if self.general[i].tickets.solved > 0:
            tot = self.general[i].tickets.totsolvingtime 
            number = self.general[i].tickets.solved
            average = tot / number
        else: 
            average = None
        return dict(
            assigned=self.general[i].tickets.assigned,
            solved=self.general[i].tickets.solved,
            revoked=self.general[i].tickets.revoked,
            averagesolvingtime=_convertTimeDiff(average))

    def getCommitsByCategory(self):
        from allura.model.project import TroveCategory

        by_cat = {}
        for entry in self.general:
            cat = entry.category
            i = getElementIndex(entry.commits, language = None)
            if i is None: 
                n, lines = 0, 0
            else: 
                n, lines = entry.commits[i].number, entry.commits[i].lines
            if cat != None:
                cat = TroveCategory.query.get(_id = cat)
            by_cat[cat] = dict(number=n, lines=lines)
        return by_cat

    #For the moment, commit stats by language are not used, since each project
    #can be linked to more than one programming language and we don't know how
    #to which programming language should be credited a line of code modified
    #within a project including two or more languages.
    def getCommitsByLanguage(self):
        langlist = []
        by_lang = {}
        i = getElementIndex(self.general, category=None)
        if i is None: 
            return dict(number=0, lines=0)
        return dict([(el.language, dict(lines=el.lines, number=el.number))
                     for el in self.general[i].commits])

    def getArtifactsByCategory(self, detailed=False):
        from allura.model.project import TroveCategory

        by_cat = {}
        for entry in self.general:
            cat = entry.category
            if cat != None: 
                cat = TroveCategory.query.get(_id = cat)
            if detailed: 
                by_cat[cat] = entry.messages
            else:
                i = getElementIndex(entry.messages, messagetype=None)
                if i is not None:
                    by_cat[cat] = entry.messages[i]
                else: 
                    by_cat[cat] = dict(created=0, modified=0)
        return by_cat

    def getArtifactsByType(self, category=None):
        i = getElementIndex(self.general, category = category)
        if i is None: 
            return {}
        entry = self.general[i].messages
        by_type = dict([(el.messagetype, dict(created=el.created,
                                              modified=el.modified))
                         for el in entry])
        return by_type

    def getTicketsByCategory(self):
        from allura.model.project import TroveCategory

        by_cat = {}
        for entry in self.general:
            cat = entry.category
            if cat != None:
                cat = TroveCategory.query.get(_id = cat)
            a, s = entry.tickets.assigned, entry.tickets.solved
            r, time = entry.tickets.solved, entry.tickets.totsolvingtime
            if s:
                average = time / s
            else:
                average = None
            by_cat[cat] = dict(
                assigned=a,
                solved=s,
                revoked=r, 
                averagesolvingtime=_convertTimeDiff(average))
        return by_cat

    def getLastMonthCommits(self, category = None):
        self.checkOldArtifacts() 
        lineslist = [el.lines for el in self.lastmonth.commits
                     if category in el.categories + [None]]
        return dict(number=len(lineslist), lines=sum(lineslist))

    def getLastMonthCommitsByCategory(self):
        from allura.model.project import TroveCategory

        self.checkOldArtifacts() 
        seen = set()
        catlist=[el.category for el in self.general
                 if el.category not in seen and not seen.add(el.category)]

        by_cat = {}
        for cat in catlist:
            lineslist = [el.lines for el in self.lastmonth.commits
                         if cat in el.categories + [None]]
            n = len(lineslist)
            lines = sum(lineslist)
            if cat != None:
                cat = TroveCategory.query.get(_id = cat)
            by_cat[cat] = dict(number=n, lines=lines)
        return by_cat

    def getLastMonthCommitsByLanguage(self):
        from allura.model.project import TroveCategory

        self.checkOldArtifacts() 
        seen = set()
        langlist=[el.language for el in self.general
                  if el.language not in seen and not seen.add(el.language)]

        by_lang = {}
        for lang in langlist:
            lineslist = [el.lines for el in self.lastmonth.commits
                         if lang in el.programming_languages + [None]]
            n = len(lineslist)
            lines = sum(lineslist)
            if lang != None:
                lang = TroveCategory.query.get(_id = lang)
            by_lang[lang] = dict(number=n, lines=lines)
        return by_lang

    def getLastMonthArtifacts(self, category = None, art_type = None):
        self.checkOldArtifacts() 
        cre, mod = reduce(
            addtuple, 
            [(int(el.created),1-int(el.created))
                for el in self.lastmonth.messages
                if (category is None or category in el.categories) and 
                (el.messagetype == art_type or art_type is None)], 
            (0,0))
        return dict(created=cre, modified=mod)

    def getLastMonthArtifactsByType(self, category = None):
        self.checkOldArtifacts()
        seen = set()
        types=[el.messagetype for el in self.lastmonth.messages
               if el.messagetype not in seen and not seen.add(el.messagetype)]

        by_type = {}
        for t in types:
            cre, mod = reduce(
                addtuple, 
                [(int(el.created),1-int(el.created))
                 for el in self.lastmonth.messages
                 if el.messagetype == t and
                 category in [None]+el.categories],
                (0,0))
            by_type[t] = dict(created=cre, modified=mod)
        return by_type

    def getLastMonthArtifactsByCategory(self):
        from allura.model.project import TroveCategory

        self.checkOldArtifacts() 
        seen = set()
        catlist=[el.category for el in self.general
                 if el.category not in seen and not seen.add(el.category)]

        by_cat = {}
        for cat in catlist:
            cre, mod = reduce(
                addtuple, 
                [(int(el.created),1-int(el.created))
                 for el in self.lastmonth.messages 
                 if cat in el.categories + [None]], (0,0))
            if cat != None:
                cat = TroveCategory.query.get(_id = cat)
            by_cat[cat] = dict(created=cre, modified=mod)
        return by_cat

    def getLastMonthTickets(self, category = None):
        from allura.model.project import TroveCategory

        self.checkOldArtifacts()
        a = len([el for el in self.lastmonth.assignedtickets
                 if category in el.categories + [None]])
        r = len([el for el in self.lastmonth.revokedtickets
                 if category in el.categories + [None]])
        s, time = reduce(
            addtuple, 
            [(1, el.solvingtime)
             for el in self.lastmonth.solvedtickets
             if category in el.categories + [None]],
            (0,0))
        if category!=None:
            category = TroveCategory.query.get(_id=category)
        if s > 0:
            time = time / s
        else:
            time = None
        return dict(
            assigned=a,
            revoked=r,
            solved=s, 
            averagesolvingtime=_convertTimeDiff(time))
        
    def getLastMonthTicketsByCategory(self):
        from allura.model.project import TroveCategory

        self.checkOldArtifacts()
        seen = set()
        catlist=[el.category for el in self.general
                 if el.category not in seen and not seen.add(el.category)]
        by_cat = {}
        for cat in catlist:
            a = len([el for el in self.lastmonth.assignedtickets
                     if cat in el.categories + [None]])
            r = len([el for el in self.lastmonth.revokedtickets
                     if cat in el.categories + [None]])
            s, time = reduce(addtuple, [(1, el.solvingtime)
                                        for el in self.lastmonth.solvedtickets
                                        if cat in el.categories+[None]],(0,0))
            if cat != None:
                cat = TroveCategory.query.get(_id = cat)
            if s > 0: 
                time = time / s
            else:
                time = None
            by_cat[cat] = dict(
                assigned=a,
                revoked=r,
                solved=s, 
                averagesolvingtime=_convertTimeDiff(time))
        return by_cat
        
    def checkOldArtifacts(self):
        now = datetime.utcnow()
        for m in self.lastmonth.messages:
            if now - m.datetime > timedelta(30):
                self.lastmonth.messages.remove(m)
        for t in self.lastmonth.assignedtickets:
            if now - t.datetime > timedelta(30):
                self.lastmonth.assignedtickets.remove(t)
        for t in self.lastmonth.revokedtickets:
            if now - t.datetime > timedelta(30):
                self.lastmonth.revokedtickets.remove(t)
        for t in self.lastmonth.solvedtickets:
            if now - t.datetime > timedelta(30):
                self.lastmonth.solvedtickets.remove(t)
        for c in self.lastmonth.commits:
            if now - c.datetime > timedelta(30):
                self.lastmonth.commits.remove(c)

    def addNewArtifact(self, art_type, art_datetime, project):
        self._updateArtifactsStats(art_type, art_datetime, project, "created")

    def addModifiedArtifact(self, art_type, art_datetime, project):
        self._updateArtifactsStats(art_type, art_datetime, project, "modified")

    def addAssignedTicket(self, ticket_datetime, project):
        topics = [t for t in project.trove_topic if t]
        self._updateTicketsStats(topics, 'assigned')
        self.lastmonth.assignedtickets.append(
            dict(datetime=ticket_datetime, categories=topics))

    def addRevokedTicket(self, ticket_datetime, project):
        topics = [t for t in project.trove_topic if t]
        self._updateTicketsStats(topics, 'revoked')
        self.lastmonth.revokedtickets.append(
            dict(datetime=ticket_datetime, categories=topics))
        self.checkOldArtifacts()

    def addClosedTicket(self, open_datetime, close_datetime, project):
        topics = [t for t in project.trove_topic if t]
        s_time=int((close_datetime-open_datetime).total_seconds())
        self._updateTicketsStats(topics, 'solved', s_time = s_time)
        self.lastmonth.solvedtickets.append(dict(
            datetime=close_datetime,
            categories=topics,
            solvingtime=s_time))
        self.checkOldArtifacts()

    def addCommit(self, newcommit, commit_datetime, project):
        def _computeLines(newblob, oldblob = None):
            if oldblob:
                listold = list(oldblob)
            else:
                listold = []
            if newblob:
                listnew = list(newblob)
            else:
                listnew = []

            if oldblob is None:
                lines = len(listnew)
            elif newblob and newblob.has_html_view:
                diff = difflib.unified_diff(
                    listold, listnew,
                    ('old' + oldblob.path()).encode('utf-8'),
                    ('new' + newblob.path()).encode('utf-8'))
                lines = len([l for l in diff if len(l) > 0 and l[0] == '+'])-1
            else:
                lines = 0
            return lines

        def _addCommitData(stats, topics, languages, lines):          
            lt = topics + [None]
            ll = languages + [None]
            for t in lt:
                i = getElementIndex(stats.general, category=t) 
                if i is None:
                    newstats = dict(
                        category=t,
                        commits=[],
                        messages=dict(
                            assigned=0,
                            solved=0,
                            revoked=0,
                            totsolvingtime=0),
                        tickets=[])   
                    stats.general.append(newstats)
                    i = getElementIndex(stats.general, category=t)
                for lang in ll:
                    j = getElementIndex(
                        stats.general[i]['commits'], language=lang)
                    if j is None:
                        stats.general[i]['commits'].append(dict(
                            language=lang, lines=lines, number=1))
                    else:
                        stats.general[i]['commits'][j].lines += lines
                        stats.general[i]['commits'][j].number += 1

        topics = [t for t in project.trove_topic if t]
        languages = [l for l in project.trove_language if l]

        d = newcommit.diffs
        if len(newcommit.parent_ids) > 0:
            oldcommit = newcommit.repo.commit(newcommit.parent_ids[0])

        totlines = 0
        for changed in d.changed:
            newblob = newcommit.tree.get_blob_by_path(changed)
            oldblob = oldcommit.tree.get_blob_by_path(changed)
            totlines+=_computeLines(newblob, oldblob)

        for copied in d.copied:
            newblob = newcommit.tree.get_blob_by_path(copied['new'])
            oldblob = oldcommit.tree.get_blob_by_path(copied['old'])
            totlines+=_computeLines(newblob, oldblob)

        for added in d.added:
            newblob = newcommit.tree.get_blob_by_path(added)
            totlines+=_computeLines(newblob)

        _addCommitData(self, topics, languages, totlines)

        self.lastmonth.commits.append(dict(
            datetime=commit_datetime, 
            categories=topics, 
            programming_languages=languages,
            lines=totlines))
        self.checkOldArtifacts()

    def _updateArtifactsStats(self, art_type, art_datetime, project, action):
        if action not in ['created', 'modified']: 
            return
        topics = [t for t in project.trove_topic if t]
        lt = [None] + topics
        for mtype in [None, art_type]:
            for t in lt:
                i = getElementIndex(self.general, category = t)
                if i is None:
                    msg = dict(
                        category=t,
                        commits=[],
                        tickets=dict(
                            solved=0,
                            assigned=0,
                            revoked=0,
                            totsolvingtime=0),
                        messages=[])
                    self.general.append(msg)
                    i = getElementIndex(self.general, category = t)
                j = getElementIndex(
                    self.general[i]['messages'], messagetype=mtype)
                if j is None:
                    entry = dict(messagetype=mtype, created=0, modified=0)
                    entry[action] += 1
                    self.general[i]['messages'].append(entry)
                else:
                    self.general[i]['messages'][j][action] += 1

        self.lastmonth.messages.append(dict(
            datetime=art_datetime,
            created=(action == 'created'),
            categories=topics,
            messagetype=art_type))
        self.checkOldArtifacts() 

    def _updateTicketsStats(self, topics, action, s_time = None):
        if action not in ['solved', 'assigned', 'revoked']:
            return
        lt = topics + [None]
        for t in lt:
            i = getElementIndex(self.general, category = t)
            if i is None:
                stats = dict(
                    category=t,
                    commits=[],
                    tickets=dict(
                        solved=0,
                        assigned=0,
                        revoked=0,
                        totsolvingtime=0),
                    messages=[])
                self.general.append(stats)
                i = getElementIndex(self.general, category = t)
            self.general[i]['tickets'][action] += 1 
            if action == 'solved': 
                self.general[i]['tickets']['totsolvingtime']+=s_time

def getElementIndex(el_list, **kw):
    for i in range(len(el_list)):
        for k in kw:
            if el_list[i].get(k) != kw[k]:
                break
        else:
            return i
    return None

def addtuple(l1, l2):
    a, b = l1
    x, y = l2
    return (a+x, b+y)

def _convertTimeDiff(int_seconds):
    if int_seconds is None:
        return None
    diff = timedelta(seconds = int_seconds)
    days, seconds = diff.days, diff.seconds
    hours = seconds / 3600
    seconds = seconds % 3600
    minutes = seconds / 60
    seconds = seconds % 60
    return dict(
        days=days, 
        hours=hours, 
        minutes=minutes,
        seconds=seconds)

Mapper.compile_all()