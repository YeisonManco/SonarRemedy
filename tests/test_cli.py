"""Real CLI smoke tests; all temporary fixtures remain inside the pack."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

PACK = Path(__file__).resolve().parents[1]


class CliSmoke(unittest.TestCase):
    def setUp(self):
        state_dir = PACK / '.debt-state'
        state_dir.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=state_dir)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / 'target'
        self.target.mkdir()
        (self.target / 'package.json').write_text('{}', encoding='utf-8')
        (self.target / 'a.py').write_text('x = 1\n', encoding='utf-8')
        self.export = self.root / 'export.json'
        self.export.write_text(json.dumps({'version': 1, 'revision': 'base123', 'issues': [
            {'id': 'S1', 'kind': 'coverage', 'path': 'a.py', 'line': 1}]}), encoding='utf-8')
        self.state = (self.root / 'session.json').relative_to(PACK).as_posix()

    def run_cli(self, *args, code=0):
        result = subprocess.run([sys.executable, '-B', str(PACK / 'debtpack.py'),
                                 '--state', self.state, *map(str, args)],
                                capture_output=True, text=True, cwd=PACK, check=False)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def prepare(self):
        state = self.run_cli('plan', self.target, self.export)
        return state['tasks'][0]['id']

    def test_end_to_end_missing_evidence_blocked(self):
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.target.iterdir()}
        self.assertEqual(self.run_cli('discover', self.target)['paths'], ['package.json'])
        job = self.prepare()
        self.assertEqual(self.run_cli('context', job)['task']['id'], job)
        self.run_cli('transition', job, 'running')
        self.run_cli('transition', job, 'submitted')
        evidence = self.root / 'evidence.json'
        evidence.write_text('{}', encoding='utf-8')
        self.assertEqual(self.run_cli('validate', job, evidence, '--revision', 'new123', code=2)['status'], 'blocked')
        self.assertEqual(self.run_cli('status')['tasks'][0]['status'], 'blocked')
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.target.iterdir()})

    def test_plan_refuses_overwrite(self):
        self.prepare()
        path = PACK / self.state
        before = path.read_bytes()
        self.run_cli('plan', self.target, self.export, code=2)
        self.assertEqual(before, path.read_bytes())

    def test_state_cannot_escape(self):
        self.state = '../outside.json'
        self.run_cli('plan', self.target, self.export, code=2)

    def test_attested_success_with_real_report_files(self):
        job = self.prepare()
        self.run_cli('transition', job, 'running')
        self.run_cli('transition', job, 'submitted')
        evidence = {'revision': 'new123', 'build': True, 'tests': True, 'coverage': 91,
                    'duplication': 4, 'smells': 0, 'security_issues': 0,
                    'unreviewed_hotspots': 0, 'sonar_timestamp': time.time(),
                    'validated_timestamp': time.time()}
        for key in ('build_report', 'tests_report', 'coverage_report', 'sonar_report'):
            evidence[key] = key + '.txt'
            (self.root / evidence[key]).write_text('SYNTHETIC SMOKE FIXTURE, not real validation', encoding='utf-8')
        file = self.root / 'evidence.json'
        file.write_text(json.dumps(evidence), encoding='utf-8')
        self.assertEqual(self.run_cli('validate', job, file, '--revision', 'new123')['status'], 'validated')
        self.assertEqual(len(self.run_cli('status')['results'][job]['report_hashes']), 4)
