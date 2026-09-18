"""Offline fixtures only: no installed scanner, credentials or Sonar required."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sonar_local as s

PACK = Path(__file__).resolve().parents[1]
SNAPSHOT = {"revision": "abc123", "digest": "a" * 64, "diff_digest": "b" * 64}


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=PACK / ".debt-state")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "App.sln").write_text("fixture")
        self.raw = {
            "adapter": "dotnet",
            "repo": str(self.repo),
            "solution": "App.sln",
            "url": "https://sonar.example.test",
            "trusted_url": "https://sonar.example.test",
            "project": "fixture",
            "branch": "feature/test",
            "timeout": 30,
            "poll_attempts": 2,
        }
        self.config = s.Config(self.raw)
        self.calls = []

    def runner(self, argv, cwd, env, timeout, log, redact):
        self.calls.append(argv)
        self.assertNotIn("SONAR_TOKEN", env)
        self.assertNotIn("fixture-private-value", str(argv))
        log.write_text("fixture log\n")
        if argv[1] == "test":
            results = Path(argv[argv.index("--results-directory") + 1])
            results.mkdir(exist_ok=True)
            (results / "a.trx").write_text(
                '<TestRun><ResultSummary outcome="Completed"><Counters total="2" executed="2" passed="2" failed="0" notExecuted="0"/></ResultSummary></TestRun>'
            )
            (results / "coverage.opencover.xml").write_text(
                '<CoverageSession><Summary numSequencePoints="2" visitedSequencePoints="2"/></CoverageSession>'
            )
        if argv[1:3] == ["sonarscanner", "end"]:
            report = self.repo / ".sonarqube/out/.sonar/report-task.txt"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                "projectKey=fixture\nceTaskId=task1\nserverUrl="
                + self.gateway_url
                + "\nceTaskUrl="
                + self.gateway_url
                + "/api/ce/task?id=task1\n"
            )
        return 0

    def scan(self, runner=None):
        class Gateway:
            url = "http://127.0.0.1:12345"
            credential = "ephemeral-not-sonar"

            def __enter__(inner):
                self.gateway_url = inner.url
                return inner

            def __exit__(inner, *args):
                pass

        with (
            patch.dict(os.environ, {"SONAR_TOKEN": "fixture-private-value"}),
            patch.object(s, "snapshot", return_value=SNAPSHOT),
            patch.object(s, "Gateway", return_value=Gateway()),
            patch.object(
                s,
                "collect",
                return_value={"analysis_id": "analysis1", "global_pass": False, "issues": []},
            ),
        ):
            return s.scan(
                self.config, self.root / "runs", execute=True, runner=runner or self.runner
            )

    def test_dry_run_never_invokes_runner_or_network(self):
        with (
            patch.dict(os.environ, {"SONAR_TOKEN": "fixture-private-value"}),
            patch.object(s, "Gateway", side_effect=AssertionError),
        ):
            result = s.scan(
                self.config, self.root / "runs", runner=lambda *a: self.fail("executed")
            )
        self.assertEqual(result["status"], "dry-run")
        self.assertFalse((self.root / "runs").exists())
        self.assertNotIn("fixture-private-value", json.dumps(result))

    def test_missing_token_blocks_even_dry_run(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(s.Blocked):
            s.scan(self.config, self.root / "runs")

    def test_configuration_fails_closed(self):
        for changes in (
            {"url": "http://sonar.example.test"},
            {"trusted_url": "https://other.test"},
            {"url": "https://user:pass@sonar.example.test"},
            {"solution": "../App.sln"},
            {"adapter": "node"},
            {"timeout": 0},
            {"skip_tests": True},
            {"token": "bad"},
        ):
            with self.subTest(changes=changes), self.assertRaises((s.Blocked, ValueError)):
                s.Config(dict(self.raw, **changes))

    def test_successful_fixture_records_proof_not_global_approval(self):
        result = self.scan()
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["tests"]["total"], 2)
        self.assertEqual(result["snapshot"], SNAPSHOT)
        self.assertFalse(result["sonar"]["global_pass"])
        self.assertEqual(
            [c[1] for c in self.calls], ["sonarscanner", "build", "test", "sonarscanner"]
        )
        run = Path(result["run"])
        self.assertTrue((run / "export.json").is_file())
        self.assertGreaterEqual(len(result["reports"]), 4)
        self.assertNotIn("fixture-private-value", (run / "evidence.json").read_text())

    def test_failed_tests_stop_before_upload(self):
        def fail(argv, *args):
            rc = self.runner(argv, *args)
            return 1 if argv[1] == "test" else rc

        result = self.scan(fail)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "tests")
        self.assertFalse(any(c[1:3] == ["sonarscanner", "end"] for c in self.calls))

    def test_scanner_failure_is_not_test_failure(self):
        result = self.scan(lambda *args: 1)
        self.assertEqual(result["stage"], "scanner-begin")
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["tests"])

    def test_missing_and_zero_tests_block_upload(self):
        for zero in (False, True):

            def no_tests(argv, *args, zero=zero):
                rc = self.runner(argv, *args)
                if argv[1] == "test":
                    f = Path(argv[argv.index("--results-directory") + 1]) / "a.trx"
                    if zero:
                        f.write_text(
                            '<TestRun><ResultSummary><Counters total="0" executed="0" passed="0" failed="0" notExecuted="0"/></ResultSummary></TestRun>'
                        )
                    else:
                        f.unlink()
                return rc

            with self.subTest(zero=zero):
                self.calls = []
                result = self.scan(no_tests)
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(any(c[1:3] == ["sonarscanner", "end"] for c in self.calls))

    def test_process_unavailable_or_timeout_blocks(self):
        for exc in (FileNotFoundError(), TimeoutError()):

            def fail(*args, exc=exc):
                raise exc

            with self.subTest(exc=exc):
                self.assertEqual(self.scan(fail)["status"], "blocked")

    def test_stale_report_rejected(self):
        f = self.root / "old.trx"
        f.write_text("old")
        os.utime(f, (1, 1))
        with self.assertRaises(s.Blocked):
            s.fresh(f, time.time())

    def test_redaction_covers_plain_urlencoded_and_basic(self):
        import base64
        from urllib.parse import quote

        secret = "fake/private+value"
        message = (
            secret
            + " "
            + quote(secret, safe="")
            + " "
            + base64.b64encode((secret + ":").encode()).decode()
        )
        self.assertEqual(s.redactor(secret)(message), "[REDACTED] [REDACTED] [REDACTED]")

    def test_failed_trx_with_zero_exit_is_failed_not_blocked(self):
        def runner(argv, *args):
            rc = self.runner(argv, *args)
            if argv[1] == "test":
                f = Path(argv[argv.index("--results-directory") + 1]) / "a.trx"
                f.write_text(
                    f.read_text().replace('passed="2" failed="0"', 'passed="1" failed="1"')
                )
            return rc

        self.assertEqual(self.scan(runner)["status"], "failed")

    def test_report_hash_tampering_blocks_verification(self):
        result = self.scan()
        run = Path(result["run"])
        with patch.object(s, "snapshot", return_value=SNAPSHOT):
            self.assertEqual(s.verify_run(run, self.repo)["status"], "collected")
            (run / "build.log").write_text("modified")
            with self.assertRaises(s.Blocked):
                s.verify_run(run, self.repo)

    def test_job_acceptance_does_not_require_global_clean(self):
        before = {"issues": [{"id": "S1"}], "global_pass": False}
        after = {"issues": [], "global_pass": False}
        self.assertTrue(s.job_improved("smells", "S1", before, after))
        self.assertFalse(s.job_improved("smells", "S2", before, after))
        self.assertFalse(s.job_improved("security", "S1", before, after))

    def test_verification_requires_retained_test_and_coverage_hashes(self):
        original = self.scan()
        self.assertEqual(original["status"], "collected")
        for suffix in (".trx", "coverage.opencover.xml"):
            with self.subTest(suffix=suffix):
                result = json.loads(json.dumps(original))
                run = Path(result["run"])
                result["reports"] = {
                    k: v for k, v in result["reports"].items() if not k.endswith(suffix)
                }
                (run / "evidence.json").write_text(json.dumps(result))
                with (
                    patch.object(s, "snapshot", return_value=SNAPSHOT),
                    self.assertRaises(s.Blocked),
                ):
                    s.verify_run(run, self.repo)

    def test_verification_rechecks_retained_test_counts(self):
        result = self.scan()
        run = Path(result["run"])
        result["tests"]["total"] = 999
        (run / "evidence.json").write_text(json.dumps(result))
        with patch.object(s, "snapshot", return_value=SNAPSHOT), self.assertRaises(s.Blocked):
            s.verify_run(run, self.repo)

    def test_accept_job_checks_scope_and_keeps_global_result_separate(self):
        state = {"target": str(self.repo), "revision": "a" * 64}
        task = {"kind": "smells", "issue": "S1"}
        before = {
            "snapshot": {"digest": state["revision"], "files": {"App.sln": "old"}},
            "finished": 10,
            "sonar": {"project": "fixture", "branch": "feature", "issues": [{"id": "S1"}]},
        }
        after = {
            "snapshot": {"files": {"App.sln": "new"}},
            "started": 11,
            "sonar": {
                "project": "fixture",
                "branch": "feature",
                "issues": [],
                "global_pass": False,
            },
        }
        with patch.object(s, "verify_run", side_effect=[before, after]):
            result = s.accept_job(state, task, "baseline", "after", ["App.sln"])
        self.assertEqual(result["status"], "accepted")
        self.assertFalse(result["global_pass"])
        self.assertEqual(result["changed_files"], ["App.sln"])
        with (
            patch.object(s, "verify_run", side_effect=[before, after]),
            self.assertRaises(s.Blocked),
        ):
            s.accept_job(state, task, "baseline", "after", ["Other.cs"])

    def test_snapshot_changes_block_before_upload(self):

        def runner(argv, *args):
            rc = self.runner(argv, *args)
            if argv[1] == "test":
                s.snapshot.return_value = dict(SNAPSHOT, digest="c" * 64)
            return rc

        result = self.scan(runner)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("snapshot changed", result["reason"])


if __name__ == "__main__":
    unittest.main()
