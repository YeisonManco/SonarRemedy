"""Local CLI boundary tests, injecting identity only in synthetic execution."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from test_debt_executor import trx
from test_debt_queue import PACK, QueueFixture

import debt_executor as e
import debt_queue as q
import debt_runner as r


class QueueCliTests(QueueFixture):
    def run_cli(self, *args, code=0, fixture=False):
        argv = ["--state", str(self.state), *map(str, args)]
        if fixture:
            wrapper = (
                "import sys; import debt_work; "
                'reader=lambda root:dict(root=str(root),branch="feature/queue",revision="a"*40); '
                "raise SystemExit(debt_work.main(sys.argv[1:],identity_reader=reader))"
            )
            command = [sys.executable, "-B", "-c", wrapper, *argv]
        else:
            command = [sys.executable, "-B", str(PACK / "debt_work.py"), *argv]
        result = subprocess.run(command, cwd=PACK, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, code, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def test_slice_default_is_compact_dry_run_without_git(self):
        result = self.run_cli(
            "slice", "--target", self.target, "--export", self.export, "--branch", "feature/queue"
        )
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(result["entries"], 1)
        self.assertNotIn("class A", json.dumps(result))
        self.assertFalse(self.state.exists())

    def test_missing_monitor_fails_without_creating_state(self):
        self.assertEqual(self.run_cli("monitor", code=2)["status"], "blocked")
        self.assertFalse(self.state.exists())

    def test_real_readonly_cli_and_document_opt_in(self):
        self.create()
        before = self.files()
        self.assertEqual(self.run_cli("monitor").get("entries"), 1)
        self.assertEqual(len(self.run_cli("next")["jobs"]), 1)
        self.assertEqual(self.run_cli("document")["status"], "dry-run")
        self.assertEqual(before, self.files())
        self.assertEqual(self.run_cli("document", "--execute")["status"], "documented")

    def test_fixture_cli_slice_claim_complete_and_document(self):
        self.assertEqual(
            self.run_cli(
                "slice",
                "--target",
                self.target,
                "--export",
                self.export,
                "--branch",
                "feature/queue",
                "--execute",
                fixture=True,
            )["status"],
            "created",
        )
        receipt = self.run_cli("claim", "--execute", fixture=True)
        self.assertEqual(receipt["status"], "leased")
        proposal = self.home / "proposal.json"
        proposal.write_text(json.dumps(self.proposal(receipt)), encoding="utf-8")
        self.assertEqual(self.run_cli("complete", "--proposal", proposal)["status"], "dry-run")
        result = self.run_cli("complete", "--proposal", proposal, "--execute", fixture=True)
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(self.run_cli("monitor")["states"]["proposed"], 1)

    def test_missing_git_identity_does_not_initialize_fixture_repo(self):
        self.assertEqual(
            self.run_cli(
                "slice",
                "--target",
                self.target,
                "--export",
                self.export,
                "--branch",
                "feature/queue",
                "--execute",
                code=2,
            )["status"],
            "blocked",
        )
        self.assertFalse((self.target / ".git").exists())
        self.assertFalse(self.state.exists())

    def test_deprecation_warning_on_stderr_does_not_affect_stdout_or_exit_code(self):
        self.create()
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(PACK / "debt_work.py"),
                "--state",
                str(self.state),
                "monitor",
            ],
            cwd=PACK,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["entries"], 1)
        self.assertIn("deprecated", result.stderr.lower())
        self.assertIn("sonarremedy", result.stderr.lower())

    def test_help_documents_actual_commands(self):
        result = subprocess.run(
            [sys.executable, "-B", str(PACK / "debt_work.py"), "--help"],
            capture_output=True,
            text=True,
            cwd=PACK,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        for command in ("slice", "claim", "complete", "defer", "reconcile", "monitor", "document"):
            self.assertIn(command, result.stdout)

    def test_claude_profile_flag_is_a_no_process_preview(self):
        profile = self.home / "profile.json"
        profile.write_text(json.dumps({"name": "claude-bare-api-key-v1"}))
        before = self.files()
        result = self.run_cli("run", "--provider", "claude", "--profile", profile)
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(before, self.files())

    def test_watch_cli_streams_only_bounded_readonly_progress(self):
        self.create()
        before = self.files()
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(PACK / "debt_work.py"),
                "--state",
                str(self.state),
                "watch",
                "--interval",
                ".01",
                "--duration",
                ".05",
            ],
            cwd=PACK,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        samples = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertGreater(len(samples), 0)
        self.assertTrue(all(s["status"] == "watch" for s in samples))
        self.assertNotIn("class A", result.stdout)
        self.assertEqual(before, self.files())


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

    def test_facade_default_dry_run_across_four_hosts(self):
        source = (PACK / "debt-runner.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("[switch]$Execute", source, "do not execute the old opt-out runner")
        pwsh = shutil.which("pwsh")
        self.assertIsNotNone(pwsh)
        before = self.files()
        for provider in ("opencode", "codex", "claude", "copilot"):
            result = subprocess.run(
                [
                    pwsh,
                    "-NoProfile",
                    "-File",
                    str(PACK / "debt-runner.ps1"),
                    "-Provider",
                    provider,
                    "-State",
                    str(self.state),
                ],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=self.home,
                env={"SystemRoot": __import__("os").environ["SystemRoot"], "PATH": str(self.home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "dry-run")
        self.assertEqual(before, self.files())

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
