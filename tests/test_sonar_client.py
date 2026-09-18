import copy
from datetime import datetime, timezone
import unittest

import sonar_client as c


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.now = datetime.now(timezone.utc).isoformat()
        self.responses = {
            'api/ce/task': {'task': {'id': 'task1', 'componentKey': 'fixture', 'status': 'SUCCESS', 'analysisId': 'analysis1'}},
            'api/project_analyses/search': {'analyses': [{'key': 'analysis1', 'date': self.now, 'revision': 'abc123'}]},
            'api/qualitygates/project_status': {'projectStatus': {'status': 'OK'}},
            'api/measures/component': {'component': {'key': 'fixture', 'measures': [
                {'metric': k, 'value': v} for k, v in dict(coverage='91', duplicated_lines_density='4', code_smells='0', vulnerabilities='0', security_hotspots='0', security_hotspots_reviewed='100').items()]}},
            'api/issues/search': {'paging': {'pageIndex': 1, 'pageSize': 100, 'total': 0}, 'issues': []},
            'api/hotspots/search': {'paging': {'pageIndex': 1, 'pageSize': 100, 'total': 0}, 'hotspots': []},
        }

    def get(self, path, **params):
        self.calls.append((path, params))
        return copy.deepcopy(self.responses[path])

    def collect(self):
        return c.collect(self.get, 'fixture', 'feature/test', 'task1', 'abc123', 0, attempts=2, sleep=lambda _: None)

    def test_exact_task_analysis_and_branch(self):
        result = self.collect()
        self.assertTrue(result['global_pass'])
        self.assertEqual(result['analysis_id'], 'analysis1')
        for path, params in self.calls:
            if path == 'api/ce/task':
                self.assertEqual(params, {'id': 'task1'})
            elif path == 'api/qualitygates/project_status':
                self.assertEqual(params, {'analysisId': 'analysis1'})
            else:
                self.assertEqual(params['branch'], 'feature/test')

    def test_missing_metric_never_means_zero(self):
        self.responses['api/measures/component']['component']['measures'].pop(2)
        with self.assertRaises(c.Blocked):
            self.collect()

    def test_invalid_metrics_rejected(self):
        for value in ('NaN', '-1', '101', None):
            self.responses['api/measures/component']['component']['measures'][0]['value'] = value
            with self.subTest(value=value), self.assertRaises(c.Blocked):
                self.collect()

    def test_pending_failed_wrong_task_block(self):
        for change in ({'status': 'PENDING'}, {'status': 'FAILED'}, {'id': 'other'}, {'componentKey': 'other'}):
            original = self.responses['api/ce/task']['task'].copy()
            self.responses['api/ce/task']['task'].update(change)
            with self.subTest(change=change), self.assertRaises(c.Blocked):
                self.collect()
            self.responses['api/ce/task']['task'] = original

    def test_stale_wrong_revision_or_concurrent_analysis_blocks(self):
        for change in ({'key': 'other'}, {'revision': 'other'}, {'date': '2000-01-01T00:00:00Z'}):
            original = self.responses['api/project_analyses/search']['analyses'][0].copy()
            self.responses['api/project_analyses/search']['analyses'][0].update(change)
            with self.subTest(change=change), self.assertRaises(c.Blocked):
                self.collect()
            self.responses['api/project_analyses/search']['analyses'][0] = original

    def test_concurrent_analysis_after_collection_blocks(self):
        old = self.get
        count = 0
        def get(path, **params):
            nonlocal count
            result = old(path, **params)
            if path == 'api/project_analyses/search':
                count += 1
                if count == 2:
                    result['analyses'][0]['key'] = 'new-analysis'
            return result
        self.get = get
        with self.assertRaises(c.Blocked):
            self.collect()

    def test_paginated_results_are_bounded_and_incomplete_fails(self):
        self.responses['api/issues/search']['paging']['total'] = 3001
        with self.assertRaises(c.Blocked):
            self.collect()

    def test_normalized_issue_omits_messages(self):
        self.responses['api/issues/search'] = {'paging': {'pageIndex': 1, 'pageSize': 100, 'total': 1}, 'issues': [
            {'key': 'S1', 'project': 'fixture', 'component': 'fixture:src/A.cs', 'line': 2,
             'type': 'CODE_SMELL', 'rule': 'csharpsquid:S2094', 'message': 'secret injection'}]}
        result = self.collect()
        self.assertEqual(result['issues'],
                         [{'id': 'S1', 'kind': 'smells', 'path': 'src/A.cs', 'line': 2,
                           'rule': 'csharpsquid:S2094'}])
        self.assertNotIn('secret injection', str(result))
        self.assertFalse(result['global_pass'])

    def test_missing_or_invalid_rule_blocks(self):
        base = {'key': 'S1', 'project': 'fixture', 'component': 'fixture:src/A.cs',
                'line': 2, 'type': 'CODE_SMELL'}
        for change in ({}, {'rule': ''}, {'rule': 'bad rule!'}, {'rule': 'a' * 201}, {'rule': 42}):
            item = dict(base, **change)
            self.responses['api/issues/search'] = {'paging': {'pageIndex': 1, 'pageSize': 100, 'total': 1},
                                                   'issues': [item]}
            with self.subTest(change=change), self.assertRaises(c.Blocked):
                self.collect()

    def test_foreign_project_and_missing_line_block(self):
        for item in ({'key': 'S1', 'project': 'other', 'component': 'other:a.cs', 'line': 1,
                      'type': 'CODE_SMELL', 'rule': 'csharpsquid:S2094'},
                     {'key': 'S1', 'project': 'fixture', 'component': 'fixture:a.cs',
                      'type': 'CODE_SMELL', 'rule': 'csharpsquid:S2094'}):
            self.responses['api/issues/search'] = {'paging': {'pageIndex': 1, 'pageSize': 100, 'total': 1}, 'issues': [item]}
            with self.subTest(item=item), self.assertRaises(c.Blocked):
                self.collect()

    def test_url_boundary_rejects_redirects_and_foreign_paths(self):
        for url in ('http://host', 'https://user:pass@host', 'https://host/?token=x', 'https://host/#x'):
            with self.subTest(url=url), self.assertRaises(c.Blocked):
                c.endpoint(url)
        with self.assertRaises(c.Blocked):
            c.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test')
        client = c.Client('https://sonar.example.test', 'fixture')
        for path in ('https://evil.test/api/ce/task', '//evil.test', '../api/ce/task'):
            with self.subTest(path=path), self.assertRaises(c.Blocked):
                client.get(path)


if __name__ == '__main__':
    unittest.main()
