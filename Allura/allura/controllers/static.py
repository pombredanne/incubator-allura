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
import mimetypes
import pkg_resources
from tg import expose, redirect, flash, config, validate, request, response
from tg.decorators import with_trailing_slash, without_trailing_slash
from webob import exc

from pylons import tmpl_context as c, app_globals as g
from allura.lib import helpers as h
from allura import model as M

class NewForgeController(object):

    @expose()
    @without_trailing_slash
    def markdown_to_html(self, markdown, neighborhood=None, project=None, app=None):
        """Convert markdown to html."""
        if neighborhood is None or project is None:
            raise exc.HTTPBadRequest()
        h.set_context(project, app, neighborhood=neighborhood)

        if app == 'wiki':
            html = g.markdown_wiki.convert(markdown)
        else:
            html = g.markdown.convert(markdown)
        return html
