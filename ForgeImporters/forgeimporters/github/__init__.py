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
import logging
import json
import urllib
import urllib2

from forgeimporters import base

log = logging.getLogger(__name__)


class GitHubProjectExtractor(base.ProjectExtractor):
    PAGE_MAP = {
            'project_info': 'https://api.github.com/repos/{project_name}',
            'issues': 'https://api.github.com/repos/{project_name}/issues',
            'wiki_url': 'https://github.com/{project_name}.wiki',
        }
    POSSIBLE_STATES = ('opened', 'closed')
    SUPPORTED_ISSUE_EVENTS = ('closed', 'reopened', 'assigned')
    NEXT_PAGE_URL_RE = re.compile(r'<([^>]*)>; rel="next"')

    def get_next_page_url(self, link):
        if not link:
            return
        m = self.NEXT_PAGE_URL_RE.match(link)
        return m.group(1) if m else None

    def parse_page(self, page):
        # Look at link header to handle pagination
        link = page.info().get('Link')
        next_page_url = self.get_next_page_url(link)
        return json.loads(page.read().decode('utf8')), next_page_url

    def get_page(self, page_name_or_url, **kw):
        page = super(GitHubProjectExtractor, self).get_page(page_name_or_url, **kw)
        page, next_page_url = page
        while next_page_url:
            p = super(GitHubProjectExtractor, self).get_page(next_page_url, **kw)
            p, next_page_url = p
            page += p
        self.page = page
        return self.page

    def get_summary(self):
        return self.get_page('project_info').get('description')

    def get_homepage(self):
        return self.get_page('project_info').get('homepage')

    def get_repo_url(self):
        return self.get_page('project_info').get('clone_url')

    def iter_issues(self):
        # github api doesn't allow getting closed and opened tickets in one query
        issues = []
        url = self.get_page_url('issues') + '?state={state}'
        for state in self.POSSIBLE_STATES:
            issue_list_url = url.format(
                state=state,
            )
            issues += self.get_page(issue_list_url)
        issues.sort(key=lambda x: x['number'])
        for issue in issues:
            yield (issue['number'], issue)

    def iter_comments(self, issue):
        comments_url = issue['comments_url']
        comments = self.get_page(comments_url)
        for comment in comments:
            yield comment

    def iter_events(self, issue):
        events_url = issue['events_url']
        events = self.get_page(events_url)
        for event in events:
            if event.get('event') in self.SUPPORTED_ISSUE_EVENTS:
                yield event

    def has_wiki(self):
        return self.get_page('project_info').get('has_wiki')
