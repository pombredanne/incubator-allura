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

from datetime import datetime
from operator import itemgetter
from unittest import TestCase
import mock

from ...github import tracker


class TestTrackerImporter(TestCase):
    @mock.patch.object(tracker, 'g')
    @mock.patch.object(tracker, 'c')
    @mock.patch.object(tracker, 'ThreadLocalORMSession')
    @mock.patch.object(tracker, 'session')
    @mock.patch.object(tracker, 'M')
    @mock.patch.object(tracker, 'TM')
    @mock.patch.object(tracker, 'GitHubProjectExtractor')
    def test_import_tool(self, gpe, TM, M, session, tlos, c, g):
        importer = tracker.GitHubTrackerImporter()
        importer.process_fields = mock.Mock()
        importer.process_milestones = mock.Mock()
        importer.process_comments = mock.Mock()
        importer.postprocess_milestones= mock.Mock()
        project, user = mock.Mock(), mock.Mock()
        app = project.install_app.return_value
        app.config.options.mount_point = 'mount_point'
        app.url = 'foo'
        gpe.iter_issues.return_value = [(50, mock.Mock()), (100, mock.Mock())]

        importer.import_tool(project, user, project_name='project_name',
                mount_point='mount_point', mount_label='mount_label', user_name='me')

        project.install_app.assert_called_once_with('tickets', 'mount_point', 'mount_label',
                EnableVoting=False,
                open_status_names='open',
                closed_status_names='closed',
                import_id={
                    'source': 'GitHub',
                    'project_name': 'me/project_name',
                }
            )
        gpe.iter_issues.assert_called_once()
        self.assertEqual(tlos.flush_all.call_args_list, [
                mock.call(),
                mock.call(),
            ])
        M.AuditLog.log.assert_called_once_with(
                'import tool mount_point from me/project_name on GitHub',
                project=project, user=user, url='foo')
        g.post_event.assert_called_once_with('project_updated')
        app.globals.invalidate_bin_counts.assert_called_once_with()

    def test_process_fields(self):
        ticket = mock.Mock()
        issue = {
            'title': 'title',
            'state': 'New',
            'created_at': 'created_at',
            'updated_at': 'updated_at',
            'assignee': {'login': 'owner'},
            'user': {'login': 'creator'},
            'body': 'hello',
            'labels': [{'name': 'first'}, {'name': 'second'}],
        }
        importer = tracker.GitHubTrackerImporter()
        with mock.patch.object(tracker, 'datetime') as dt:
            dt.strptime.side_effect = lambda s,f: s
            importer.process_fields(ticket, issue)
            self.assertEqual(ticket.summary, 'title')
            self.assertEqual(ticket.description, '*Originally created by:* [creator](https://github.com/creator)\n*Originally owned by:* [owner](https://github.com/owner)\n\nhello')
            self.assertEqual(ticket.status, 'New')
            self.assertEqual(ticket.created_date, 'created_at')
            self.assertEqual(ticket.mod_date, 'updated_at')
            self.assertEqual(dt.strptime.call_args_list, [
                    mock.call('created_at', '%Y-%m-%dT%H:%M:%SZ'),
                    mock.call('updated_at', '%Y-%m-%dT%H:%M:%SZ'),
                ])
            self.assertEqual(ticket.labels, ['first', 'second'])

    @mock.patch.object(tracker, 'c')
    def test_postprocess_milestones(self, c):
        importer = tracker.GitHubTrackerImporter()
        importer.open_milestones = set([
            ('first', datetime(day=23, month=4, year=2015)),
            ('second',datetime(day=25, month=4, year=2015))
        ])
        milestones = importer.postprocess_milestones()
        self.assertItemsEqual(milestones, [
                {
                    'name': '_milestone',
                    'type': 'milestone',
                    'label': 'Milestone',
                    'milestones': [
                        {'name': 'first', 'due_date': u'2015-04-23', 'complete': False},
                        {'name': 'second', 'due_date': u'2015-04-25', 'complete': False},
                    ],
                },
            ])

    def test_get_attachments(self):
        importer = tracker.GitHubTrackerImporter()
        body = 'hello\n' \
        '![cdbpzjc5ex4](https://f.cloud.github.com/assets/979771/1027411/a393ab5e-0e70-11e3-8a38-b93a3df904cf.jpg)\r\n'
        new_body, attachments = importer._get_attachments(body)
        self.assertEqual(new_body, 'hello\n')
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].url, 'https://f.cloud.github.com/assets/979771/1027411/a393ab5e-0e70-11e3-8a38-b93a3df904cf.jpg')

    def test_process_comments(self):
        ticket = mock.Mock()
        extractor = mock.Mock()
        issue = {'comments_url': '/comments'}
        extractor.iter_comments.return_value = [
                {
                    'body': 'hello',
                    'created_at': '2013-08-26T16:57:53Z',
                    'user': {'login': 'me'},
                }
            ]
        importer = tracker.GitHubTrackerImporter()
        importer.process_comments(extractor, ticket, issue)
        self.assertEqual(ticket.discussion_thread.add_post.call_args_list[0], mock.call(
                text='*Originally posted by:* [me](https://github.com/me)\nhello',
                timestamp=datetime(2013, 8, 26, 16, 57, 53),
                ignore_security=True,
            ))

    def test_process_events(self):
        ticket = mock.Mock()
        extractor = mock.Mock()
        issue = {'events_url': '/events'}
        extractor.iter_events.return_value = [
            {
                'actor': {'login': 'darth'},
                'created_at': '2013-09-12T09:58:49Z',
                'event': 'closed',
            },
            {
                'actor': {'login': 'yoda'},
                'created_at': '2013-09-12T10:13:20Z',
                'event': 'reopened',
            },
            {
                'actor': {'login': 'luke'},
                'created_at': '2013-09-12T10:14:00Z',
                'event': 'assigned',
            },
        ]
        importer = tracker.GitHubTrackerImporter()
        importer.process_events(extractor, ticket, issue)
        args = ticket.discussion_thread.add_post.call_args_list
        self.assertEqual(args[0], mock.call(
            text='*Ticket changed by:* [darth](https://github.com/darth)\n\n'
                 '- **status**: open --> closed',
            timestamp=datetime(2013, 9, 12, 9, 58, 49),
            ignore_security=True))
        self.assertEqual(args[1], mock.call(
            text='*Ticket changed by:* [yoda](https://github.com/yoda)\n\n'
                 '- **status**: closed --> open',
            timestamp=datetime(2013, 9, 12, 10, 13, 20),
            ignore_security=True))
        self.assertEqual(args[2], mock.call(
            text='- **assigned_to**: [luke](https://github.com/luke)',
            timestamp=datetime(2013, 9, 12, 10, 14, 0),
            ignore_security=True))
