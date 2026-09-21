"""Local CLI boundary tests: debt_runner.py driven directly (in-process), not
through any subprocess CLI. debt_work.py's direct CLI has been removed; its
subprocess-driven tests (QueueCliTests) were removed with it."""

import sys
from pathlib import Path
from unittest.mock import patch

from test_debt_executor import trx
from test_debt_queue import QueueFixture

import debt_executor as e
import debt_queue as q
import debt_runner as r


class RunnerTests(QueueFixture):
    def config(self):
        return {
            "version": 1,
            "target": str(self.target),
            "branch": "feature/queue",
            "policy": "characterization",
            "characterization_reason": "Explicit fixture-only whitespace refactor policy.",
            "test_paths": [],
            "expected_red": {},
            "allowed_outputs": [],
            "checks": [
                {
                    "name": "build",
                    "kind": "build",
                    "argv": [sys.executable, "-B", "-c", "pass"],
                    "executable_sha256": q.digest(Path(sys.executable).read_bytes()),
                    "cwd": ".",
                    "timeout_seconds": 10,
                },
                {
                    "name": "tests",
                    "kind": "trx",
                    "argv": [sys.executable, "-B", "-c", "pass", "{run}"],
                    "executable_sha256": q.digest(Path(sys.executable).read_bytes()),
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "report": "tests.trx",
                },
            ],
        }

    def factory(self, context):
        source = context["sources"][0]
        return {
            "version": 1,
            "job_id": context["job_id"],
            "attempt_id": context["attempt_id"],
            "lease": context["lease"],
            "context_fingerprint": context["context_fingerprint"],
            "status": "proposed",
            "reason": "",
            "risks": [],
            "follow_up": [],
            "test_plan": "Characterization fixture only; no model runs tests.",
            "edits": [
                {
                    "path": source["path"],
                    "before_sha256": source["sha256"],
                    "replacements": [{"old": "Value()", "new": "Value( )"}],
                }
            ],
        }

    def check(self, argv, cwd, **kwargs):
        if argv[-1] != "pass":
            (Path(argv[-1]) / "tests.trx").write_text(trx())
        return {"status": "exited", "reason": "", "exit_code": 0, "stdout": b""}

    def test_runner_dry_run_no_files_or_process_for_all_providers(self):
        before = self.files()
        for provider in ("manual", "opencode", "codex", "claude", "copilot"):
            result = r.run(self.queue(), provider=provider)
            self.assertEqual(result["status"], "dry-run")
        self.assertEqual(before, self.files())

    def test_native_execute_unavailable_without_state_or_auth_probe(self):
        before = self.files()
        with patch.object(q.Queue, "_open", side_effect=AssertionError("opened queue")):
            for provider in ("opencode", "codex", "claude", "copilot"):
                self.assertEqual(
                    r.run(self.queue(), provider=provider, execute=True)["status"], "unavailable"
                )
        self.assertEqual(before, self.files())

    def test_manual_wait_and_resume_never_duplicate_claim(self):
        self.create()
        result = r.run(self.queue(), execute=True)
        self.assertEqual(result["status"], "awaiting_proposals")
        receipt = result["waiting"][0]
        before = self.files()
        resumed = r.run(self.queue(), execute=True, resume=True)
        self.assertEqual(resumed["waiting"][0]["attempt_id"], receipt["attempt_id"])
        self.assertEqual(before, self.files())

    def test_1205_findings_more_than_eight_files_integrate_across_batches_and_resume(self):
        issues = []
        for i in range(12):
            name = f"f{i:02d}.cs"
            (self.target / name).write_text("class A { int Value() => 1; }\n")
            issues.extend(self.issue(f"S{i}-{j}", name) for j in range(100 if i else 105))
        self.write_export(issues)
        self.create()
        config = self.config()
        e.configure(self.queue(), config, approved_sha256=q.digest(q.encoded(config)), execute=True)
        first = r.run(
            self.queue(),
            execute=True,
            integrate=True,
            max_batches=1,
            proposal_factory=self.factory,
            process_runner=self.check,
            control_root=self.home / "control",
        )
        self.assertEqual(first["status"], "batch_limit")
        second = r.run(
            self.queue(),
            execute=True,
            resume=True,
            integrate=True,
            proposal_factory=self.factory,
            process_runner=self.check,
            control_root=self.home / "control",
        )
        self.assertEqual(second["status"], "quiescent")
        progress = self.queue().monitor()
        self.assertEqual(progress["entries"], 1205)
        self.assertEqual(progress["states"]["locally_verified"], 12)
        before = self.files()
        self.assertEqual(
            r.run(
                self.queue(),
                execute=True,
                resume=True,
                integrate=True,
                process_runner=self.check,
                control_root=self.home / "control",
            )["status"],
            "quiescent",
        )
        self.assertEqual(before, self.files())

    def test_all_deferred_quiescent_and_invalid_manual_result_does_not_loop(self):
        self.write_export([self.issue(kind="hotspots")])
        self.create()
        self.assertEqual(r.run(self.queue(), execute=True)["status"], "quiescent")
        self.state = self.home / "other-queue"
        self.write_export([self.issue()])
        self.create()
        result = r.run(
            self.queue(), execute=True, proposal_factory=lambda context: {"wrong_id": "x"}
        )
        self.assertEqual(result["status"], "quiescent")
        self.assertEqual(self.queue().monitor()["states"]["failed"], 1)

    def test_partial_wrong_id_and_oversized_manual_results_fail_without_retry(self):
        for case in ("partial", "wrong_id", "oversized"):
            self.state = self.home / case
            self.create()

            def invalid(context, case=case):
                result = self.factory(context)
                if case == "partial":
                    result["status"] = "partial"
                elif case == "wrong_id":
                    result["job_id"] = "wrong"
                else:
                    result["test_plan"] = "x" * (q.MAX_RESULT + 1)
                return result

            with self.subTest(case=case):
                self.assertEqual(
                    r.run(self.queue(), execute=True, proposal_factory=invalid)["status"],
                    "quiescent",
                )
                self.assertEqual(self.queue().monitor()["states"]["failed"], 1)

    def test_integrated_failure_stops_sibling_writing(self):
        self.write_export([self.issue(), self.issue("S2", "b.cs")])
        self.create()
        config = self.config()
        e.configure(self.queue(), config, approved_sha256=q.digest(q.encoded(config)), execute=True)
        calls = []
        original = {name: (self.target / name).read_bytes() for name in ("a.cs", "b.cs")}

        def fail(argv, cwd, **kwargs):
            calls.append(argv)
            result = self.check(argv, cwd, **kwargs)
            if len(calls) == 3:
                result["exit_code"] = 1
            return result

        result = r.run(
            self.queue(),
            execute=True,
            integrate=True,
            proposal_factory=self.factory,
            process_runner=fail,
            control_root=self.home / "control",
        )
        self.assertEqual(result["status"], "quarantined")
        self.assertEqual(len(calls), 3)
        changed = [
            name for name, before in original.items() if (self.target / name).read_bytes() != before
        ]
        self.assertEqual(
            len(changed),
            1,
            "only the attempted integration may leave edits; its sibling is untouched",
        )
