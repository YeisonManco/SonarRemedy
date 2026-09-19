"""Isolated target fixtures; synthetic TRX is not live .NET/Sonar evidence."""

import hashlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_debt_queue import QueueFixture

import debt_executor as e
import debt_queue as q


def trx(passed=1, failed=0, skipped=0, message="Assert.Equal() Failure", name="Behavior.Value"):
    outcome = "Failed" if failed else "Completed"
    result = "Failed" if failed else "NotExecuted" if skipped else "Passed"
    return (
        f'<TestRun><Results><UnitTestResult testName="{name}" outcome="{result}">'
        f"<Output><ErrorInfo><Message>{message}</Message></ErrorInfo></Output></UnitTestResult></Results>"
        f'<ResultSummary outcome="{outcome}"><Counters total="{passed + failed + skipped}" executed="{passed + failed}" '
        f'passed="{passed}" failed="{failed}" notExecuted="{skipped}"/></ResultSummary></TestRun>'
    )


class ExecutorTests(QueueFixture):
    def setUp(self):
        super().setUp()
        (self.target / "tests.cs").write_text("expected=1\n")
        self.create(write_sets={"a.cs": ["a.cs", "tests.cs"]})
        self.control = self.home / "control"
        self.calls = []
        exe_sha = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        self.config = {
            "version": 1,
            "target": str(self.target),
            "branch": "feature/queue",
            "policy": "red-first",
            "characterization_reason": "",
            "test_paths": ["tests.cs"],
            "expected_red": {"Behavior.Value": "Assert.Equal() Failure"},
            "allowed_outputs": [],
            "checks": [
                {
                    "name": "build",
                    "kind": "build",
                    "argv": [sys.executable, "-B", "-c", "pass"],
                    "executable_sha256": exe_sha,
                    "cwd": ".",
                    "timeout_seconds": 10,
                },
                {
                    "name": "tests",
                    "kind": "trx",
                    "argv": [sys.executable, "-B", "-c", "pass", "{run}"],
                    "executable_sha256": exe_sha,
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "report": "tests.trx",
                },
            ],
        }

    def configure(self):
        result = e.configure(
            self.queue(),
            self.config,
            approved_sha256=q.digest(q.encoded(self.config)),
            execute=True,
        )
        self.assertEqual(result["status"], "configured")

    def proposed(self, version=2, follow_up=None):
        receipt = self.leased()
        proposal = self.proposal(receipt)
        if version == 2:
            proposal["version"] = 2
            proposal["edits"][0]["phase"] = "implementation"
            proposal["edits"].append(
                {
                    "path": "tests.cs",
                    "before_sha256": q.digest((self.target / "tests.cs").read_bytes()),
                    "phase": "test",
                    "replacements": [{"old": "expected=1", "new": "expected=3"}],
                }
            )
        if follow_up is not None:
            proposal["follow_up"] = follow_up
        self.queue().complete(proposal, execute=True, now=101)
        return receipt["job_id"]

    def runner(self, argv, cwd, **kwargs):
        self.calls.append(argv)
        if argv[-1] == "pass":
            return {"status": "exited", "exit_code": 0, "reason": "", "stdout": b""}
        report = Path(argv[-1]) / "tests.trx"
        expected = int((self.target / "tests.cs").read_text().split("=")[1])
        actual = 3 if "=> 3" in (self.target / "a.cs").read_text() else 1
        failed = int(expected != actual)
        report.write_text(trx(passed=1 - failed, failed=failed), encoding="utf-8")
        return {"status": "exited", "exit_code": failed, "reason": "", "stdout": b""}

    def test_configuration_requires_explicit_reviewed_hash(self):
        with self.assertRaises(q.Blocked):
            e.configure(self.queue(), self.config, approved_sha256="0" * 64, execute=True)

    def test_dry_run_does_not_touch_target_state_or_process(self):
        before = self.files()
        self.assertEqual(e.integrate(self.queue(), "unused", execute=False)["status"], "dry-run")
        self.assertEqual(before, self.files())

    def test_version_two_test_and_implementation_proposal_is_supported(self):
        receipt = self.leased()
        proposal = self.proposal(receipt)
        proposal["version"] = 2
        proposal["edits"][0]["phase"] = "implementation"
        caught = None
        try:
            result = self.queue().complete(proposal, execute=True, now=101)
        except q.Blocked as error:
            caught = error
            result = None
        self.assertIsNone(
            caught, "v2 phases must be validated rather than rejected as an unknown version"
        )
        self.assertEqual(result["status"], "proposed")

    def test_red_green_post_checks_are_serial_and_status_only_locally_verified(self):
        self.configure()
        job = self.proposed()
        outcome = e.integrate(
            self.queue(), job, execute=True, control_root=self.control, process_runner=self.runner
        )
        self.assertEqual(outcome["status"], "locally_verified")
        self.assertEqual(len(self.calls), 8)
        self.assertIn("=> 3", (self.target / "a.cs").read_text())
        progress = self.queue().monitor()
        self.assertEqual(progress["states"]["locally_verified"], 1)
        self.assertEqual(progress["states"]["sonar_confirmed"], 0)
        before = self.files()
        self.assertEqual(
            e.integrate(
                self.queue(),
                job,
                execute=True,
                control_root=self.control,
                process_runner=self.runner,
            )["status"],
            "already_locally_verified",
        )
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(before, self.files())

    def test_report_surfaces_follow_up(self):
        self.configure()
        job = self.proposed(
            follow_up=[
                {"action": "set_env_var", "name": "DB_PASSWORD", "note": "set it in the pipeline"}
            ]
        )
        e.integrate(
            self.queue(), job, execute=True, control_root=self.control, process_runner=self.runner
        )
        report = self.queue().report()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["applied_count"], 1)
        self.assertEqual(len(report["follow_up_required"]), 1)
        follow_up = report["follow_up_required"][0]["follow_up"][0]
        self.assertEqual(follow_up["action"], "set_env_var")
        self.assertEqual(follow_up["name"], "DB_PASSWORD")

    def test_characterization_is_explicit_and_v1_without_policy_cannot_apply(self):
        self.configure()
        job = self.proposed(version=1)
        self.assertEqual(
            e.integrate(
                self.queue(),
                job,
                execute=True,
                control_root=self.control,
                process_runner=self.runner,
            )["status"],
            "deferred",
        )
        self.assertIn("=> 1", (self.target / "a.cs").read_text())

    def test_stale_full_snapshot_blocks_even_when_job_source_is_unchanged(self):
        self.configure()
        job = self.proposed()
        (self.target / "b.cs").write_text("owner change\n")
        with self.assertRaises(q.Blocked):
            e.integrate(
                self.queue(),
                job,
                execute=True,
                control_root=self.control,
                process_runner=self.runner,
            )
        self.assertEqual(self.calls, [])

    def test_snapshot_records_unreadable_files_without_crashing(self):
        from unittest.mock import patch

        (self.target / "locked.cs").write_text("x")
        real_read = q.read_bytes

        def flaky(path, limit):
            if Path(path).name == "locked.cs":
                raise PermissionError("fixture locked")
            return real_read(path, limit)

        with patch.object(q, "read_bytes", side_effect=flaky):
            snap = e.snapshot(self.target)
        self.assertTrue(snap["locked.cs"].startswith("unreadable:"))
        with patch.object(q, "read_bytes", side_effect=flaky):
            self.assertEqual(snap, e.snapshot(self.target))

    def test_snapshot_excludes_regenerated_outputs(self):
        bindir = self.target / "bin" / "Debug"
        bindir.mkdir(parents=True)
        (bindir / "app.dll").write_bytes(b"\x00" * 1024)
        vsdir = self.target / ".vs" / "proj"
        vsdir.mkdir(parents=True)
        (vsdir / "index.vsidx").write_text("x")
        snap = e.snapshot(self.target)
        self.assertTrue(any(key.endswith("a.cs") for key in snap))
        self.assertFalse(any(part in ("bin", ".vs") for key in snap for part in key.split("/")))
        self.assertEqual(snap, e.snapshot(self.target))

    def test_failed_build_persists_failure_receipt(self):
        self.configure()
        job = self.proposed()

        def fail_build(argv, cwd, **kwargs):
            return {"status": "exited", "exit_code": 1, "reason": "", "stdout": b"MSB3021 locked"}

        # Baseline runs on the unmodified tree: the room is at fault, so the
        # job stays proposed for retry instead of being stranded as deferred.
        with self.assertRaisesRegex(q.Blocked, "baseline_build_failed"):
            e.integrate(
                self.queue(),
                job,
                execute=True,
                control_root=self.control,
                process_runner=fail_build,
            )
        self.assertEqual(self.queue().monitor()["states"].get("proposed", 0), 1)
        matches = list(Path(self.state).rglob("*.failed.json"))
        self.assertEqual(len(matches), 1)
        data = q.parse_json(q.read_bytes(matches[0], q.MAX_EXPORT))
        self.assertEqual(data["exit_code"], 1)
        self.assertIn("MSB3021", data["stdout_tail"])
        self.assertEqual(data["name"], "build")
        # The barrier must be cleared: a re-raise would otherwise leave an
        # orphaned active.json that blocks the next integrate.
        barrier = self.control / q.digest(str(self.target).casefold().encode())
        self.assertFalse((barrier / "active.json").exists())

    def test_failed_integrated_check_quarantines_and_preserves_preimages(self):
        self.configure()
        job = self.proposed()

        def fail_green(argv, cwd, **kwargs):
            result = self.runner(argv, cwd, **kwargs)
            if len(self.calls) == 5:
                result["exit_code"] = 1
            return result

        result = e.integrate(
            self.queue(), job, execute=True, control_root=self.control, process_runner=fail_green
        )
        self.assertEqual(result["status"], "quarantined")
        self.assertTrue(self.queue().monitor()["quarantined"])
        self.assertTrue(list(self.state.rglob("*.preimage")))
        self.assertIn(
            "=> 3", (self.target / "a.cs").read_text(), "no guessed rollback is permitted"
        )

    def test_unexpected_check_write_quarantines_before_any_patch(self):
        self.configure()
        job = self.proposed()

        def contamination(argv, cwd, **kwargs):
            (self.target / "outside.cs").write_text("unexpected\n")
            return self.runner(argv, cwd, **kwargs)

        result = e.integrate(
            self.queue(), job, execute=True, control_root=self.control, process_runner=contamination
        )
        self.assertEqual(result["status"], "quarantined")
        self.assertIn("=> 1", (self.target / "a.cs").read_text())

    def test_wrong_red_provenance_quarantines_not_green_acceptance(self):
        self.configure()
        job = self.proposed()

        def wrong_red(argv, cwd, **kwargs):
            result = self.runner(argv, cwd, **kwargs)
            if result["exit_code"] == 1:
                (Path(argv[-1]) / "tests.trx").write_text(
                    trx(passed=0, failed=1, message="compiler infrastructure failed")
                )
            return result

        result = e.integrate(
            self.queue(), job, execute=True, control_root=self.control, process_runner=wrong_red
        )
        self.assertEqual(result["status"], "quarantined")
        self.assertIn("=> 1", (self.target / "a.cs").read_text())

    def test_trx_missing_zero_skipped_stale_nonzero_exit_and_wrong_failure_rejected(self):
        report = self.home / "test.trx"
        for text, exit_code in [
            (trx(passed=0), 0),
            (trx(passed=0, skipped=1), 0),
            (trx(), 1),
            (trx(passed=0, failed=1), 0),
        ]:
            report.write_text(text)
            with self.subTest(text=text, exit_code=exit_code), self.assertRaises(q.Blocked):
                e.parse_trx(report, 0, exit_code)
        report.write_text(trx())
        with self.assertRaises(q.Blocked):
            e.parse_trx(report, time.time_ns() + 10**9, 0)

    def test_trx_green_and_exact_expected_red(self):
        report = self.home / "test.trx"
        report.write_text(trx())
        self.assertIsNotNone(e.parse_trx(report, 0, 0))
        report.write_text(trx(passed=0, failed=1))
        self.assertIsNotNone(
            e.parse_trx(report, 0, 1, {"Behavior.Value": "Assert.Equal() Failure"})
        )

    def test_trx_duplicate_test_names_ok_for_characterization(self):
        report = self.home / "dup.trx"
        dup = (
            "<TestRun><Results>"
            '<UnitTestResult testName="Behavior.Value" outcome="Passed" />'
            '<UnitTestResult testName="Behavior.Value" outcome="Passed" />'
            "</Results>"
            '<ResultSummary outcome="Completed">'
            '<Counters total="2" executed="2" passed="2" failed="0" notExecuted="0" />'
            "</ResultSummary></TestRun>"
        )
        report.write_text(dup)
        # characterization (no expected_red) tolerates duplicate test names
        self.assertIsNotNone(e.parse_trx(report, 0, 0))

    def test_retained_verification_cannot_disappear_without_blocking(self):
        self.configure()
        job = self.proposed()
        result = e.integrate(
            self.queue(), job, execute=True, control_root=self.control, process_runner=self.runner
        )
        Path(result["evidence"]).unlink()
        with self.assertRaises(q.Blocked):
            self.queue().monitor()

    def test_branch_change_during_baseline_is_contamination(self):
        self.configure()
        job = self.proposed()
        identity = self.identity
        changed = [False]

        def reader(root):
            return dict(identity(root), branch="other" if changed[0] else "feature/queue")

        def mutate(argv, cwd, **kwargs):
            changed[0] = True
            return self.runner(argv, cwd, **kwargs)

        work = q.Queue(self.state, identity_reader=reader)
        result = e.integrate(
            work, job, execute=True, control_root=self.control, process_runner=mutate
        )
        self.assertEqual(result["status"], "quarantined")

    def test_trx_future_timestamp_is_not_fresh_evidence(self):
        report = self.home / "future.trx"
        report.write_text(trx())
        os.utime(report, (time.time() + 3600, time.time() + 3600))
        with self.assertRaises(q.Blocked):
            e.parse_trx(report, 0, 0)

    def test_shared_barrier_survives_interruption_and_is_not_queue_local(self):
        with e.TargetBarrier(self.target, self.control) as barrier:
            barrier.activate({"queue": str(self.state)})
            with self.assertRaises(q.Blocked):
                with e.TargetBarrier(self.target, self.control):
                    self.fail("same target acquired twice")
        with self.assertRaises(q.Blocked), e.TargetBarrier(self.target, self.control):
            self.fail("interrupted barrier must survive process ownership release")

    def test_characterization_policy_executes_without_fabricated_red(self):
        self.config.update(
            policy="characterization",
            characterization_reason="Approved behavior-preserving fixture refactor.",
            expected_red={},
            test_paths=[],
        )
        self.configure()
        receipt = self.leased()
        proposal = self.proposal(receipt)
        proposal["edits"][0]["replacements"] = [{"old": "Value()", "new": "Value( )"}]
        self.queue().complete(proposal, execute=True, now=101)
        result = e.integrate(
            self.queue(),
            receipt["job_id"],
            execute=True,
            control_root=self.control,
            process_runner=self.runner,
        )
        self.assertEqual(result["status"], "locally_verified")
        self.assertEqual(len(self.calls), 6)
        self.assertFalse(any(p.name == "red.json" for p in self.state.rglob("*.json")))

    def test_executor_preserves_bom_and_crlf_bytes(self):
        self.state = self.home / "bom-queue"
        (self.target / "a.cs").write_bytes(b"\xef\xbb\xbfclass A { int Value() => 1; }\r\n")
        (self.target / "tests.cs").write_bytes(b"expected=1\r\n")
        self.create(write_sets={"a.cs": ["a.cs", "tests.cs"]})
        self.configure()
        result = e.integrate(
            self.queue(),
            self.proposed(),
            execute=True,
            control_root=self.control,
            process_runner=self.runner,
        )
        self.assertEqual(result["status"], "locally_verified")
        self.assertEqual(
            (self.target / "a.cs").read_bytes(), b"\xef\xbb\xbfclass A { int Value() => 3; }\r\n"
        )

    def test_real_contained_check_children_execute_red_green(self):
        template = trx(passed=0, failed=1)
        code = (
            "import pathlib,re,sys; root=pathlib.Path(sys.argv[1]); "
            'expected=int((root/"tests.cs").read_text().split("=")[1]); '
            'actual=int(re.search(r"=> (\d+)",(root/"a.cs").read_text()).group(1)); '
            "failed=int(expected!=actual); "
            f"pathlib.Path(sys.argv[2]).write_text({template!r} if failed else {trx()!r}); "
            "sys.exit(failed)"
        )
        self.config["checks"][1]["argv"] = [
            sys.executable,
            "-B",
            "-c",
            code,
            "{target}",
            "{run}/tests.trx",
        ]
        self.configure()
        result = e.integrate(self.queue(), self.proposed(), execute=True, control_root=self.control)
        self.assertEqual(result["status"], "locally_verified")


class ChecksExampleTests(unittest.TestCase):
    """The documented template must carry the exact required shape, and a fully
    substituted copy must satisfy the validator (the satisfiable path exists)."""

    def test_example_template_has_required_shape(self):
        import json

        pack = Path(__file__).resolve().parents[1]
        example = json.loads((pack / "examples" / "debt-checks.example.json").read_text())
        self.assertEqual(
            set(example),
            {
                "version",
                "target",
                "branch",
                "policy",
                "characterization_reason",
                "test_paths",
                "expected_red",
                "allowed_outputs",
                "checks",
            },
        )
        self.assertEqual(example["checks"][0]["kind"], "build")
        self.assertIn("executable_sha256", example["checks"][0])

    def test_substituted_example_passes_validation(self):
        import json
        import tempfile

        pack = Path(__file__).resolve().parents[1]
        example = json.loads((pack / "examples" / "debt-checks.example.json").read_text())
        exe_sha = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as d:
            example["target"] = d
            example["branch"] = "main"
            for check in example["checks"]:
                check["argv"] = [sys.executable, "-B", "-c", "pass"]
                check["executable_sha256"] = exe_sha
            root = e.validate_config(example)
            self.assertEqual(str(root), str(q.canonical_case(d)))


class BarrierReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.target = self.home / "target"
        self.target.mkdir()
        (self.target / "a.cs").write_text("class A {}\n", encoding="utf-8")
        self.control = self.home / "control"
        self.before = e.snapshot(self.target)

    def _orphan(self, mutate=False):
        job = self.home / "state" / "jobs" / "j1" / "attempts" / "a1"
        integration = job / "integration"
        integration.mkdir(parents=True)
        (job / "job.json").write_text("{}", encoding="utf-8")
        (job / "result.json").write_text("{}", encoding="utf-8")
        (integration / "intent.json").write_text(
            q.encoded({"job_id": "j1", "before": self.before, "write_paths": ["a.cs"]}).decode(),
            encoding="utf-8",
        )
        folder = self.control / q.digest(str(self.target).casefold().encode())
        folder.mkdir(parents=True)
        (folder / "active.json").write_text(
            q.encoded({"job_id": "j1", "intent": str(integration / "intent.json")}).decode(),
            encoding="utf-8",
        )
        if mutate:
            (self.target / "a.cs").write_text("class A { changed }\n", encoding="utf-8")
        return folder

    def test_nothing_to_release(self):
        result = e.release_barrier(self.target, control_root=self.control)
        self.assertEqual(result["status"], "ok")

    def test_verified_orphan_releases_preserving_proposal(self):
        folder = self._orphan()
        dry = e.release_barrier(self.target, control_root=self.control)
        self.assertEqual(dry["status"], "blocked")
        self.assertIn("orphan", dry["reason"])
        result = e.release_barrier(self.target, execute=True, control_root=self.control)
        self.assertEqual(result["status"], "released")
        self.assertFalse((folder / "active.json").exists())
        attempt = self.home / "state" / "jobs" / "j1" / "attempts" / "a1"
        self.assertFalse((attempt / "integration").exists())
        self.assertTrue((attempt / "job.json").is_file())
        self.assertTrue((attempt / "result.json").is_file())

    def test_changed_tree_refuses_without_touching(self):
        folder = self._orphan(mutate=True)
        result = e.release_barrier(self.target, execute=True, control_root=self.control)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("manual_review", result["reason"])
        self.assertTrue((folder / "active.json").is_file())

    def test_real_quarantine_is_never_released(self):
        folder = self._orphan()
        (folder / "quarantine.json").write_text("{}", encoding="utf-8")
        result = e.release_barrier(self.target, execute=True, control_root=self.control)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("quarantine", result["reason"])
        self.assertTrue((folder / "active.json").is_file())

    def test_missing_intent_refuses(self):
        folder = self.control / q.digest(str(self.target).casefold().encode())
        folder.mkdir(parents=True)
        (folder / "active.json").write_text("{}", encoding="utf-8")
        result = e.release_barrier(self.target, execute=True, control_root=self.control)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue((folder / "active.json").is_file())
