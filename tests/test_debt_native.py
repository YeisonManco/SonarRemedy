"""Actual fake Python children through native argv/supervisor, not LLM callbacks."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

import debt_queue as q
import debt_transport as t
import debt_runner as r
import debt_executor as e
from test_debt_queue import PACK, QueueFixture
import test_debt_queue_cli as cli_fixtures


class NativeTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.profile = dict(version=1, name='claude-bare-api-key-v1', provider='claude', executable=sys.executable,
                            executable_sha256=t.executable_digest(sys.executable), cli_version='2.1.139',
                            model='claude-sonnet-4-6', destination='https://api.anthropic.com', auth_env='ANTHROPIC_API_KEY',
                            timeout_seconds=2, max_budget_usd=1)
        self.intervals = []
        self.lock = threading.Lock()

    def native_process(self, argv, cwd, **kwargs):
        # The factory still constructs production Claude argv. Only this test
        # execution seam selects a local Python fixture instead of the native exe.
        result = t.run_process([sys.executable, '-B', str(PACK/'tests/fake_claude.py'), *argv[1:]], cwd, **kwargs)
        if result.get('stdout', b'').startswith(b'{'):
            value = json.loads(result['stdout'])
            if '_fixture_start_ns' in value:
                with self.lock:
                    self.intervals.append((value['_fixture_start_ns'], value['_fixture_end_ns']))
                self.assertFalse(value['_fixture_sonar_present'])
        return result

    def native_run(self, **kwargs):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fixture-approved-key', 'SONAR_TOKEN': 'fixture-sonar-private'}):
            return r.run(self.queue(), provider='claude', profile=self.profile, execute=True,
                         native_process_runner=self.native_process, **kwargs)

    def test_real_parallel_dispatch_and_no_integration_before_all_workers_finish(self):
        self.write_export([self.issue(), self.issue('S2', 'b.cs')])
        self.create()
        config = cli_fixtures.RunnerTests.config(self)
        e.configure(self.queue(), config, approved_sha256=q.digest(q.encoded(config)), execute=True)
        def check(argv, cwd, **kwargs):
            self.assertEqual(len(self.intervals), 2, 'all proposal children must finish before any check')
            return cli_fixtures.RunnerTests.check(self, argv, cwd, **kwargs)
        result = self.native_run(limit=2, integrate=True, process_runner=check, control_root=self.home/'control')
        self.assertEqual(result['status'], 'quiescent')
        self.assertEqual(self.queue().monitor()['states']['locally_verified'], 2)
        self.assertLess(max(a for a, b in self.intervals), min(b for a, b in self.intervals), 'real children must overlap')

    def test_1205_findings_real_process_batches_resume_without_duplicate_effects(self):
        issues = []
        for i in range(12):
            name = f'f{i:02}.cs'
            (self.target/name).write_text('class A { int Value() => 1; }\n')
            issues.extend(self.issue(f'S{i}-{j}', name) for j in range(105 if i == 0 else 100))
        self.write_export(issues)
        self.create()
        config = cli_fixtures.RunnerTests.config(self)
        e.configure(self.queue(), config, approved_sha256=q.digest(q.encoded(config)), execute=True)
        options = dict(limit=3, integrate=True, process_runner=lambda *a, **kw: cli_fixtures.RunnerTests.check(self, *a, **kw),
                       control_root=self.home/'control')
        self.assertEqual(self.native_run(max_batches=1, **options)['status'], 'batch_limit')
        self.assertEqual(self.native_run(resume=True, **options)['status'], 'quiescent')
        self.assertEqual(self.queue().monitor()['entries'], 1205)
        self.assertEqual(self.queue().monitor()['states']['locally_verified'], 12)
        self.assertEqual(len(self.intervals), 12)
        self.assertEqual(self.native_run(resume=True, **options)['status'], 'quiescent')
        self.assertEqual(len(self.intervals), 12)
        events = sorted([(a, 1) for a, b in self.intervals] + [(b, -1) for a, b in self.intervals])
        concurrent = peak = 0
        for _, delta in events:
            concurrent += delta
            peak = max(peak, concurrent)
        self.assertGreaterEqual(peak, 2)
        self.assertLessEqual(peak, 3)

    def test_model_failures_timeout_junk_partial_wrong_identity_secret_and_size_continue(self):
        names = ['timeout.cs', 'junk.cs', 'partial.cs', 'wrong.cs', 'leak.cs', 'huge.cs', 'deferred.cs']
        for name in names:
            (self.target/name).write_text('class A { int Value() => 1; }\n')
        self.write_export([self.issue(f'S{i}', name) for i, name in enumerate(names)])
        self.create()
        result = self.native_run(limit=3)
        self.assertEqual(result['status'], 'quiescent')
        progress = self.queue().monitor()
        self.assertEqual(progress['states']['failed'], 6)
        self.assertEqual(progress['states']['deferred'], 1)
        for path in self.state.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'fixture-approved-key', path.read_bytes())
                self.assertNotIn(b'fixture-sonar-private', path.read_bytes())

    def test_watch_is_bounded_read_only_and_no_source(self):
        self.create()
        before = self.files()
        samples = list(r.watch(self.queue(), interval=.01, duration=.05))
        self.assertGreater(len(samples), 0)
        self.assertLessEqual(len(samples), 6)
        self.assertNotIn('class A', json.dumps(samples))
        self.assertEqual(before, self.files())

    def test_cached_native_outcome_resumes_without_second_process(self):
        self.create()
        receipt = self.queue().claim(execute=True)
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fixture-approved-key'}):
            result = t.dispatch(self.profile, receipt, process_runner=self.native_process)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.queue().monitor()['states']['leased'], 1)
        self.assertEqual(self.native_run(resume=True)['status'], 'proposals_ready')
        self.assertEqual(len(self.intervals), 1)

    def test_interrupted_marker_does_not_relaunch_or_auto_retry(self):
        self.create()
        receipt = self.queue().claim(execute=True)
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fixture-approved-key'}):
            t.dispatch(self.profile, receipt, process_runner=self.native_process)
        Path(receipt['context_path']).with_name('native-outcome.json').unlink()
        self.assertEqual(self.native_run(resume=True)['status'], 'reconciliation_required')
        self.assertEqual(len(self.intervals), 1)

    def test_unexpected_native_target_write_stops_before_any_integration(self):
        (self.target/'contaminate.cs').write_text('class A { int Value() => 1; }\n')
        self.write_export([self.issue(path='contaminate.cs')])
        self.create()
        result = self.native_run()
        self.assertEqual(result['status'], 'quarantined')
        self.assertTrue(self.queue().monitor()['quarantined'])
        self.assertEqual(self.queue().monitor()['states']['locally_verified'], 0)

    def test_watch_rejects_unbounded_configuration(self):
        for options in ({'interval': 0}, {'duration': float('inf')}, {'duration': -1}):
            with self.subTest(options=options), self.assertRaises(q.Blocked):
                list(r.watch(self.queue(), **options))
