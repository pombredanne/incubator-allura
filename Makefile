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

# Allura Makefile
SHELL=/bin/bash

-include Makefile.def

# Constants
PID_PATH?=.

# Targets
test:
ifdef BB
# running on buildbot (Makefile.def.buildbot sets BB to 1)
# setup pysvn
	-[ ! -f $(VIRTUAL_ENV)/lib/python2.7/site-packages/pysvn ] && ln -s /usr/lib64/python2.7/site-packages/pysvn $(VIRTUAL_ENV)/lib/python2.7/site-packages/
	-[ ! -d $(VIRTUAL_ENV)/lib/python2.7/site-packages/pysvn-1.7.5-py2.7.egg-info ] && mkdir $(VIRTUAL_ENV)/lib/python2.7/site-packages/pysvn-1.7.5-py2.7.egg-info
# rebuild apps
	./rebuild-all.bash
endif
	ALLURA_VALIDATION=none ./run_tests
	./run_clonedigger

run:
	paster serve --reload Allura/development.ini
