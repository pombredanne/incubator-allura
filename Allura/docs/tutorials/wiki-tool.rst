..     Licensed to the Apache Software Foundation (ASF) under one
       or more contributor license agreements.  See the NOTICE file
       distributed with this work for additional information
       regarding copyright ownership.  The ASF licenses this file
       to you under the Apache License, Version 2.0 (the
       "License"); you may not use this file except in compliance
       with the License.  You may obtain a copy of the License at

         http://www.apache.org/licenses/LICENSE-2.0

       Unless required by applicable law or agreed to in writing,
       software distributed under the License is distributed on an
       "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
       KIND, either express or implied.  See the License for the
       specific language governing permissions and limitations
       under the License.

Creating your first Allura Tool
=====================================================================

Adding your Allura Tool to a Allura Install
=====================================================================

Writing a Wiki Tool Part 1: Pages
=====================================================================

Writing a wiki Tool Part 2: Links
=====================================================================

Writing a wiki Tool Part 3: Revisions
=====================================================================

Testing your Tool
===========================

Testing the controllers and models of an Allura tool is fairly
straightforward.  Generally, you should follow the example of tests in the
`allura/tests/functional` directory for controller tests and
`allura.tests.model` for model tests.  For functional tests, the Allura platform
provides a convenient "test harness" :class:`allura.controllers.test.TestController` controller
class which is used as the application root for the
:class:`allura.tests.TestController` class.

In order to test your new tool controllers, you simply need to use the `self.app.get()`
and `self.app.post()` methods of your test controller.  The test harness makes
all the tools available in the system available under the URL /*entry point
name*/.  So to test the :mod:`allura.ext.project_home` tool, for instance, we
need only write the following::

    from allura.tests import TestController

    class TestProjectHome(TestController):

        def test_home(self):
            r = self.app.get('/home/')

Whenever you use the :class:`allura.tests.TestController` app property, the
test harness sets up the context so that `c.project` is always the
`projects/test` project and whichever tool name you request is mounted at its
entry point (so the Wiki tool will be mounted at /Wiki/).  `c.user` is always
set to the `test-admin` user to avoid authentication issues.

The framework used to generate the WSGI environment for testing your tools is
provided by the `WebTest <http://pythonpaste.org/webtest/>`_ module, where you can
find further documentation for the `.get()` and `.post()` methods.

Testing Allura models is also straightforward, though it usually requires
setting the pylons context object `c` before your test.  An example of this
technique follows::

    import mock
    from pylons import tmpl_context as c, app_globals as g

    from allura.lib.app_globals import Globals
    from allura import model as M

    def setUp():
        g._push_object(Globals())
        c._push_object(mock.Mock())
        g.set_project('projects/test')
        g.set_app('hello')
        c.user = M.User.query.get(username='test-admin')

Testing the tasks and events is  similar to testing models.  Generally, you will
simply want to call your `@task` and `@event_handler` methods directly rather
than setting up a full mocking infrastructure, though it is possible to use the
MonQTask model in the allura model if you wish to do more functional/integration testing.
