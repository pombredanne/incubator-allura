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

import re
import unittest

from pylons import tmpl_context as c, app_globals as g
import mock
from ming.base import Object

from allura import app
from allura.lib.app_globals import Globals
from allura import model as M

def setUp():
    g._push_object(Globals())
    c._push_object(mock.Mock())
    c.user._id = None
    c.project = mock.Mock()
    c.project.name = 'Test Project'
    c.project.shortname = 'tp'
    c.project._id = 'testproject/'
    c.project.database_uri = 'mim://nosetest:project'
    c.project.url = lambda: '/testproject/'
    app_config = mock.Mock()
    app_config._id = None
    app_config.project_id = 'testproject/'
    app_config.tool_name = 'tool'
    app_config.options = Object(mount_point = 'foo')
    c.app = mock.Mock()
    c.app.config = app_config
    c.app.config.script_name = lambda:'/testproject/test_application/'
    c.app.config.url = lambda:'http://testproject/test_application/'
    c.app.url = c.app.config.url()
    c.app.__version__ = '0.0'

def test_config_options():
    options = [
        app.ConfigOption('test1', str, 'MyTestValue'),
        app.ConfigOption('test2', str, lambda:'MyTestValue')]
    assert options[0].default == 'MyTestValue'
    assert options[1].default == 'MyTestValue'

def test_sitemap():
    sm = app.SitemapEntry('test', '')[
        app.SitemapEntry('a', 'a/'),
        app.SitemapEntry('b', 'b/')]
    sm[app.SitemapEntry(lambda app:app.config.script_name(), 'c/')]
    bound_sm = sm.bind_app(c.app)
    assert bound_sm.url == 'http://testproject/test_application/', bound_sm.url
    assert bound_sm.children[-1].label == '/testproject/test_application/', bound_sm.children[-1].label
    assert len(sm.children) == 3
    sm.extend([app.SitemapEntry('a', 'a/')[
                app.SitemapEntry('d', 'd/')]])
    assert len(sm.children) == 3

