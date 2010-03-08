import logging

from tg import expose, validate, redirect
from pylons import g, c, request
from formencode import validators
from pymongo.bson import ObjectId

from ming.orm.base import session

from pyforge.app import Application, ConfigOption, SitemapEntry, DefaultAdminController
from pyforge.lib.security import require, has_artifact_access
from pyforge.model import ProjectRole
from pyforge.lib.search import search
from pyforge.lib import helpers as h

from .forum import ForumController
from forgeforum import model
from forgeforum import widgets as FW

log = logging.getLogger(__name__)

class RootController(object):

    class W(object):
        forum_subscription_form=FW.ForumSubscriptionForm()

    @expose('forgeforum.templates.index')
    def index(self):
        c.forum_subscription_form = self.W.forum_subscription_form
        return dict(forums=model.Forum.query.find(dict(
                app_config_id=c.app.config._id,
                parent_id=None)))
                  
    @expose('forgeforum.templates.search')
    @validate(dict(q=validators.UnicodeString(if_empty=None),
                   history=validators.StringBool(if_empty=False)))
    def search(self, q=None, history=None):
        'local plugin search'
        results = []
        count=0
        if not q:
            q = ''
        else:
            search_query = '''%s
            AND is_history_b:%s
            AND mount_point_s:%s''' % (
                q, history, c.app.config.options.mount_point)
            results = search(search_query)
            if results: count=results.hits
        return dict(q=q, history=history, results=results or [], count=count)

    def _lookup(self, id, *remainder):
        return ForumController(id), remainder

    @h.vardec
    @expose()
    @validate(W.forum_subscription_form)
    def subscribe(self, **kw):
        import pdb; pdb.set_trace()
        if forum is None: forum = []
        if thread is None: thread = []
        objs = []
        for data in forum:
            objs.append(dict(obj=model.Forum.query.get(shortname=data['shortname']),
                             subscribed=bool(data.get('subscribed'))))
        for data in thread:
            objs.append(dict(obj=model.Thread.query.get(_id=data['id']),
                             subscribed=bool(data.get('subscribed'))))
        for obj in objs:
            if obj['subscribed']:
                obj['obj'].subscriptions[str(c.user._id)] = True
            else:
                obj['obj'].subscriptions.pop(str(c.user._id), None)
        redirect(request.referer)

