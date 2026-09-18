"""Queue fixtures only: no real Git mutation, target repository or providers."""

import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import debt_queue as q

PACK = Path(__file__).resolve().parents[1]
REVISION = "a" * 40


class QueueFixture(unittest.TestCase):
    def setUp(self):
        base = PACK / ".debt-state"
        base.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="queue-core-", dir=base)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.target = self.home / "target"
        self.target.mkdir()
        (self.target / "a.cs").write_text("class A { int Value() => 1; }\n", encoding="utf-8")
        (self.target / "b.cs").write_text("class B { int Value() => 2; }\n", encoding="utf-8")
        self.export = self.home / "export.json"
        self.state = self.home / "queue"
        self.write_export([self.issue()])

    def issue(self, name="S1", path="a.cs", kind="smells", **fields):
        return dict(id=name, path=path, kind=kind, line=1, rule="csharp:S1", **fields)

    def write_export(self, entries):
        self.export.write_text(
            json.dumps({"version": 1, "revision": REVISION, "issues": entries}), encoding="utf-8"
        )

    def identity(self, target):
        return {"root": str(target), "branch": "feature/queue", "revision": REVISION}

    def create(self, **kwargs):
        return q.slice_queue(
            self.target,
            self.export,
            self.state,
            "feature/queue",
            execute=True,
            identity_reader=self.identity,
            **kwargs,
        )

    def queue(self, **kwargs):
        return q.Queue(self.state, identity_reader=self.identity, **kwargs)

    def leased(self, **kwargs):
        receipt = self.queue().claim(execute=True, now=100, **kwargs)
        self.assertIsNotNone(receipt, "an eligible job must be leased")
        self.assertEqual(receipt["status"], "leased")
        return receipt

    def proposal(self, receipt):
        context = json.loads(Path(receipt["context_path"]).read_text(encoding="utf-8"))
        return {
            "version": 1,
            "job_id": receipt["job_id"],
            "attempt_id": receipt["attempt_id"],
            "lease": receipt["lease"],
            "context_fingerprint": receipt["context_fingerprint"],
            "status": "proposed",
            "edits": [
                {
                    "path": "a.cs",
                    "before_sha256": context["sources"][0]["sha256"],
                    "replacements": [{"old": "=> 1", "new": "=> 3"}],
                }
            ],
            "reason": "",
            "risks": [],
            "test_plan": "Add a regression test; no tests executed by this proposal.",
        }

    def files(self):
        return {
            str(p.relative_to(self.home)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.home.rglob("*")
            if p.is_file()
        }


class IntakeTests(QueueFixture):
    def test_1205_issues_are_preserved_in_path_kind_group(self):
        self.write_export([self.issue("S" + str(i)) for i in range(1205)])
        preview = q.plan(self.target, self.export, "feature/queue")
        self.assertEqual(len(preview["entries"]), 1205)
        self.assertEqual(len(preview["jobs"]), 1)
        self.assertEqual(len(preview["jobs"][0]["issues"]), 1205)
        self.assertEqual({i["ordinal"] for i in preview["entries"]}, set(range(1205)))

    def test_3000_bound_is_inclusive_and_never_truncates(self):
        self.write_export([self.issue("S" + str(i)) for i in range(3000)])
        self.assertEqual(len(q.plan(self.target, self.export, "feature/queue")["entries"]), 3000)
        self.write_export([self.issue("S" + str(i)) for i in range(3001)])
        with self.assertRaises(q.Blocked):
            q.plan(self.target, self.export, "feature/queue")

    def test_duplicates_and_case_aliases_defer_all_affected_entries(self):
        self.write_export(
            [
                self.issue(),
                self.issue(path="b.cs"),
                self.issue("S3", "A.cs"),
                self.issue("S4", "b.cs", "coverage"),
            ]
        )
        entries = q.plan(self.target, self.export, "feature/queue")["entries"]
        self.assertEqual(len(entries), 4)
        self.assertTrue(all(e["status"] == "deferred" for e in entries[:3]))
        self.assertEqual(entries[3]["status"], "pending")

    def test_every_unusable_entry_is_visible_without_raw_messages(self):
        missing = self.issue("S2")
        missing.pop("rule")
        self.write_export(
            [
                None,
                missing,
                self.issue("S3", "../escape.cs"),
                self.issue("S4", "missing.cs"),
                self.issue("S5", kind="hotspots"),
                self.issue("S6", kind="unknown", message="PRIVATE BODY"),
            ]
        )
        preview = q.plan(self.target, self.export, "feature/queue")
        self.assertEqual(len(preview["entries"]), 6)
        self.assertTrue(all(e["status"] == "deferred" and e["reason"] for e in preview["entries"]))
        self.assertNotIn("PRIVATE BODY", json.dumps(preview))

    def test_slice_dry_run_never_calls_identity_or_creates_state(self):
        result = q.slice_queue(
            self.target,
            self.export,
            self.state,
            "feature/queue",
            identity_reader=lambda _: self.fail("Git in dry-run"),
        )
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(len(result["entries"]), 1)
        self.assertFalse(self.state.exists())


class LifecycleTests(QueueFixture):
    def test_pending_lists_jobs_with_paths_and_kinds(self):
        self.write_export(
            [
                self.issue("S1", path="a.cs", kind="coverage"),
                self.issue("S2", path="b.cs", kind="smells"),
            ]
        )
        self.create()
        pending = self.queue().pending()
        self.assertEqual(len(pending), 2)
        self.assertEqual({j["path"] for j in pending}, {"a.cs", "b.cs"})
        for job in pending:
            self.assertIn("kind", job)
            self.assertIn("write_paths", job)
            self.assertIn("issue_count", job)

    def test_jobs_after_first_eight_and_2000_ordinals_are_durable(self):
        issues = []
        for i in range(12):
            name = f"file{i}.cs"
            (self.target / name).write_text(f"class C{i} {{ }}\n")
            issues.append(self.issue("S" + str(i), name))
        self.write_export(issues)
        self.create()
        self.assertEqual(self.queue().monitor()["jobs"], 12)
        self.assertEqual(len(self.queue().next()), 4)
        self.state = self.home / "queue-3000"
        self.write_export([self.issue("S" + str(i)) for i in range(3000)])
        self.create()
        self.assertEqual(self.queue().monitor()["entries"], 3000)

    def test_empty_metric_only_export_cannot_claim_debt_reduction(self):
        self.export.write_text(
            json.dumps({"version": 1, "revision": REVISION, "issues": [], "coverage": 50})
        )
        self.create()
        self.assertEqual(self.queue().next(), [])
        self.assertIsNone(self.queue().monitor()["debt_reduction"])
        self.assertEqual(self.queue().monitor()["states"]["sonar_confirmed"], 0)

    def test_ambiguous_json_and_oversized_export_are_not_partial_success(self):
        for text in ('{"version":1,"version":1}', '{"version":NaN}', " " * (q.MAX_EXPORT + 1)):
            self.export.write_text(text)
            with self.subTest(length=len(text)), self.assertRaises(q.Blocked):
                self.create()
            self.assertFalse(self.state.exists())

    def test_failed_initialization_and_orphan_claim_surface_blocking(self):
        with patch.object(q, "write_immutable", side_effect=OSError("fixture write failure")):
            with self.assertRaises(OSError):
                self.create()
        with self.assertRaises(q.Blocked):
            self.queue().next()
        self.state = self.home / "orphan-queue"
        self.create()
        with patch.object(q.Queue, "_set_status", side_effect=OSError("fixture claim crash")):
            with self.assertRaises(OSError):
                self.queue().claim(execute=True, now=100)
        with self.assertRaisesRegex(q.Blocked, "orphan_attempt"):
            self.queue().next()

    def test_actual_repeated_old_text_is_rejected(self):
        (self.target / "a.cs").write_text("class A { int A; int B; }\n")
        self.create()
        receipt = self.leased()
        proposal = self.proposal(receipt)
        proposal["edits"][0]["replacements"] = [{"old": "int", "new": "long"}]
        with self.assertRaisesRegex(q.Blocked, "not_unique"):
            self.queue().complete(proposal, execute=True, now=101)

    def test_replacement_outside_retained_window_is_rejected(self):
        (self.target / "a.cs").write_text("".join(f"// line {i}\n" for i in range(1, 6001)))
        issue = self.issue()
        issue["line"] = 3000
        self.write_export([issue])
        self.create()
        receipt = self.leased()
        proposal = self.proposal(receipt)
        proposal["edits"][0]["replacements"] = [{"old": "// line 1\n", "new": "// changed\n"}]
        with self.assertRaisesRegex(q.Blocked, "retained_context"):
            self.queue().complete(proposal, execute=True, now=101)

    def test_sqlite_connection_failure_is_a_bounded_blocked_outcome(self):
        self.create()
        caught = None
        with patch.object(
            q.sqlite3, "connect", side_effect=sqlite3.OperationalError("fixture locked")
        ):
            try:
                self.queue().monitor()
            except Exception as error:
                caught = error
        self.assertIsInstance(caught, q.Blocked)

    def test_unverified_filesystem_platform_is_unavailable(self):
        target = self.target
        caught = None
        with patch.object(q.os, "name", "unknown-platform"):
            try:
                q.local_path(target)
            except Exception as error:
                caught = error
        self.assertIsInstance(caught, q.Blocked)

    def test_missing_ordinal_cannot_report_full_accounting(self):
        self.create()
        with closing(sqlite3.connect(self.state / "queue.sqlite3")) as connection:
            with connection:
                connection.execute("DELETE FROM entries WHERE ordinal=0")
        with self.assertRaises(q.Blocked):
            self.queue().monitor()

    def test_new_result_cannot_exceed_total_artifact_budget(self):
        self.create()
        receipt = self.leased()
        used = sum(p.stat().st_size for p in self.state.rglob("*") if p.is_file())
        with patch.object(q, "MAX_ARTIFACTS", used + 64), self.assertRaises(q.Blocked):
            self.queue().complete(self.proposal(receipt), execute=True, now=101)
        self.assertFalse(Path(receipt["context_path"]).with_name("result.json").exists())

    def test_all_state_paths_checked_before_sqlite_opens(self):
        self.create()
        marker = self.state / "unexpected"
        marker.write_text("fixture")
        original = q.local_path
        opened = []
        real_connect = q.sqlite3.connect

        def check(value, **kwargs):
            if Path(value) == marker:
                raise q.Blocked("linked_ancestor")
            return original(value, **kwargs)

        def connect(*args, **kwargs):
            opened.append(True)
            return real_connect(*args, **kwargs)

        with (
            patch.object(q, "local_path", side_effect=check),
            patch.object(q.sqlite3, "connect", side_effect=connect),
            self.assertRaises(q.Blocked),
        ):
            self.queue().monitor()
        self.assertEqual(
            opened,
            [],
            "unsafe journal/state siblings must be rejected before SQLite can inspect them",
        )

    def test_document_contains_attempt_evidence_locators_not_lease(self):
        self.create()
        receipt = self.leased()
        self.queue().complete(self.proposal(receipt), execute=True, now=101)
        self.queue().document(execute=True)
        report = json.loads((self.state / "report.json").read_text())
        self.assertIn("attempts", report)
        self.assertEqual(report["attempts"][0]["attempt_id"], receipt["attempt_id"])
        self.assertNotIn(receipt["lease"], json.dumps(report))

    def test_next_counts_files_and_skips_deferred_priority_stage(self):
        self.write_export(
            [
                self.issue("H", kind="hotspots"),
                self.issue("S1"),
                self.issue("S2"),
                self.issue("S3", "b.cs"),
            ]
        )
        self.create()
        batch = self.queue().next()
        self.assertEqual(len(batch), 2)
        self.assertEqual(sum(j["issue_count"] for j in batch), 3)
        self.assertTrue(all(j["kind"] == "smells" for j in batch))

    def test_readonly_queries_never_create_files_or_invoke_git(self):
        self.create()
        before = self.files()
        queue = q.Queue(self.state, identity_reader=lambda _: self.fail("readonly identity call"))
        with patch.object(q.subprocess, "run", side_effect=AssertionError("subprocess")):
            self.assertEqual(len(queue.next()), 1)
            self.assertEqual(queue.monitor()["entries"], 1)
        self.assertEqual(before, self.files())

    def test_missing_database_read_does_not_create_anything(self):
        with self.assertRaises(q.Blocked):
            self.queue().monitor()
        self.assertFalse(self.state.exists())

    def test_mutations_require_execute_and_dry_run_has_no_artifacts(self):
        self.create()
        before = self.files()
        self.assertEqual(self.queue().claim()["status"], "dry-run")
        self.assertEqual(self.queue().document()["status"], "dry-run")
        self.assertEqual(before, self.files())

    def test_claim_materializes_exact_hashed_source_without_target_write(self):
        self.create()
        before = (self.target / "a.cs").read_bytes()
        receipt = self.leased()
        context = json.loads(Path(receipt["context_path"]).read_text(encoding="utf-8"))
        self.assertEqual(context["sources"][0]["sha256"], hashlib.sha256(before).hexdigest())
        self.assertTrue(context["sources"][0]["whole_file"])
        self.assertEqual(context["sources"][0]["windows"][0]["start_line"], 1)
        self.assertEqual(context["sources"][0]["windows"][0]["text"].encode(), before)
        self.assertEqual(len(context["issues"]), 1)
        self.assertEqual(receipt["context_fingerprint"], context["context_fingerprint"])
        self.assertEqual(before, (self.target / "a.cs").read_bytes())

    def test_large_file_uses_honest_line_windows_and_full_hash(self):
        content = "".join(f"// line {i}\n" for i in range(1, 6001))
        (self.target / "a.cs").write_text(content, encoding="utf-8", newline="")
        issue = self.issue()
        issue["line"] = 3000
        self.write_export([issue])
        self.create()
        receipt = self.leased()
        context = json.loads(Path(receipt["context_path"]).read_text())
        source = context["sources"][0]
        self.assertFalse(source["whole_file"])
        self.assertEqual(source["sha256"], hashlib.sha256(content.encode()).hexdigest())
        self.assertIn("// line 3000\n", source["windows"][0]["text"])
        self.assertGreater(source["windows"][0]["start_line"], 1)
        self.assertLess(Path(receipt["context_path"]).stat().st_size, q.MAX_CONTEXT)

    def test_context_overflow_defers_without_losing_1205_issues(self):
        self.write_export([self.issue("S" + str(i)) for i in range(1205)])
        self.create()
        outcome = self.queue().claim(execute=True, now=100)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome["status"], "deferred")
        self.assertEqual(outcome["reason"], "context_budget_exceeded")
        self.assertEqual(self.queue().monitor()["entry_states"]["deferred"], 1205)

    def test_same_write_set_locks_across_kind_and_shared_test_path(self):
        self.write_export(
            [self.issue("S1"), self.issue("S2", kind="coverage"), self.issue("S3", "b.cs")]
        )
        self.create(write_sets={"b.cs": ["b.cs", "a.cs"]})
        self.leased()
        self.assertEqual(self.queue().next(), [])

    def test_concurrent_real_process_claims_do_not_duplicate_lease(self):
        self.create()
        code = (
            "import json,sys; import debt_queue as q; "
            'reader=lambda root:dict(root=str(root),branch="feature/queue",revision="a"*40); '
            "print(json.dumps(q.Queue(sys.argv[1],identity_reader=reader).claim(execute=True,now=100)))"
        )
        children = [
            subprocess.Popen(
                [sys.executable, "-B", "-c", code, str(self.state)],
                cwd=PACK,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        outputs = []
        for child in children:
            out, err = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, err)
            outputs.append(json.loads(out))
        self.assertEqual(sum(v is not None for v in outputs), 1)
        self.assertEqual(self.queue().monitor()["states"]["leased"], 1)

    def test_completion_is_proposal_only_idempotent_and_immutable(self):
        self.create()
        receipt = self.leased()
        proposal = self.proposal(receipt)
        first = self.queue().complete(proposal, execute=True, now=101)
        self.assertEqual(first["status"], "proposed")
        before = self.files()
        self.assertEqual(self.queue().complete(proposal, execute=True, now=102), first)
        self.assertEqual(before, self.files())
        progress = self.queue().monitor()
        self.assertEqual(progress["states"]["proposed"], 1)
        self.assertEqual(progress["states"]["locally_verified"], 0)
        self.assertEqual(progress["states"]["sonar_confirmed"], 0)
        self.assertIsNone(progress["debt_reduction"])

    def test_completion_rejects_wrong_identity_partial_and_success_flags(self):
        self.create()
        receipt = self.leased()
        valid = self.proposal(receipt)
        for field, value in [
            ("lease", "wrong"),
            ("job_id", "wrong"),
            ("attempt_id", "wrong"),
            ("context_fingerprint", "0" * 64),
            ("status", "success"),
            ("status", "partial"),
            ("all_fixed", True),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises(q.Blocked):
                self.queue().complete(dict(valid, **{field: value}), execute=True, now=101)
        self.assertEqual(self.queue().monitor()["states"]["leased"], 1)

    def test_source_hash_mismatch_prevents_completion(self):
        self.create()
        receipt = self.leased()
        proposal = self.proposal(receipt)
        (self.target / "a.cs").write_text("changed by owner\n")
        with self.assertRaises(q.Blocked):
            self.queue().complete(proposal, execute=True, now=101)

    def test_stale_claim_defers_and_independent_job_continues(self):
        self.write_export([self.issue(), self.issue("S2", "b.cs")])
        self.create()
        job = (
            next(j for j in self.queue().next() if j["path"] == "a.cs")
            if self.queue().next()
            else None
        )
        self.assertIsNotNone(job)
        (self.target / "a.cs").write_text("changed\n")
        self.assertEqual(self.queue().claim(job["job_id"], execute=True)["status"], "deferred")
        self.assertEqual(len(self.queue().next()), 1)

    def test_expiry_requires_explicit_reconcile_and_does_not_retry(self):
        self.create()
        receipt = self.leased(lease_seconds=10)
        with self.assertRaises(q.Blocked):
            self.queue().complete(self.proposal(receipt), execute=True, now=110)
        self.assertEqual(self.queue().next(), [])
        result = self.queue().reconcile(receipt, effects="none", execute=True, now=111)
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(self.queue().next(), [])
        with self.assertRaises(q.Blocked):
            self.queue().complete(self.proposal(receipt), execute=True, now=112)

    def test_unknown_expired_effects_quarantine_queue(self):
        self.write_export([self.issue(), self.issue("S2", "b.cs")])
        self.create()
        receipt = self.leased(lease_seconds=10)
        self.assertEqual(
            self.queue().reconcile(receipt, effects="unknown", execute=True, now=111)["status"],
            "failed",
        )
        self.assertTrue(self.queue().monitor()["quarantined"])
        self.assertEqual(self.queue().next(), [])

    def test_artifact_write_failure_keeps_lease_and_retry_is_explicit(self):
        self.create()
        receipt = self.leased()
        proposal = self.proposal(receipt)
        with patch.object(q, "write_immutable", side_effect=OSError("fixture disk failure")):
            with self.assertRaises((q.Blocked, OSError)):
                self.queue().complete(proposal, execute=True, now=101)
        self.assertEqual(self.queue().monitor()["states"]["leased"], 1)
        self.assertEqual(
            self.queue().complete(proposal, execute=True, now=102)["status"], "proposed"
        )

    def test_published_result_commit_gap_recovers_without_overwrite(self):
        self.create()
        receipt = self.leased()
        proposal = self.proposal(receipt)
        with (
            patch.object(
                q.Queue, "_record_result", side_effect=OSError("fixture crash"), create=True
            ),
            self.assertRaises(OSError),
        ):
            self.queue().complete(proposal, execute=True, now=101)
        result_path = Path(receipt["context_path"]).with_name("result.json")
        original = result_path.read_bytes()
        self.assertEqual(self.queue().monitor()["reconciliation_required"], 1)
        self.assertEqual(
            self.queue().complete(proposal, execute=True, now=102)["status"], "proposed"
        )
        self.assertEqual(original, result_path.read_bytes())

    def test_immutable_result_collision_blocks_not_overwrites(self):
        self.create()
        receipt = self.leased()
        proposal = self.proposal(receipt)
        self.queue().complete(proposal, execute=True, now=101)
        changed = copy.deepcopy(proposal)
        changed["edits"][0]["replacements"][0]["new"] = "=> 4"
        with self.assertRaises(q.Blocked):
            self.queue().complete(changed, execute=True, now=102)

    def test_deferred_proposal_releases_paths_for_independent_work(self):
        self.write_export([self.issue(), self.issue("S2", "b.cs")])
        self.create()
        receipt = self.leased()
        proposal = self.proposal(receipt)
        proposal.update(status="deferred", edits=[], reason="needs_human_review")
        self.assertEqual(
            self.queue().complete(proposal, execute=True, now=101)["status"], "deferred"
        )
        self.assertEqual(len(self.queue().next()), 1)

    def test_deterministic_reports_exclude_source_and_claims_of_reduction(self):
        self.create()
        result = self.queue().document(execute=True)
        self.assertEqual(result["status"], "documented")
        before = self.files()
        self.assertEqual(result, self.queue().document(execute=True))
        self.assertEqual(before, self.files())
        report = (self.state / "report.json").read_text()
        self.assertNotIn("class A", report)
        self.assertNotIn("class A", json.dumps(self.queue().monitor()))
        self.assertIsNone(json.loads(report)["progress"]["debt_reduction"])

    def test_different_target_or_branch_binding_is_rejected(self):
        self.create()
        for settings in ({"target": self.home}, {"branch": "other"}):
            with self.subTest(settings=settings), self.assertRaises(q.Blocked):
                self.queue(**settings).monitor()

    def test_state_artifact_ancestor_reparse_is_rejected(self):
        self.create()
        with patch.object(q, "is_reparse", side_effect=lambda info: True):
            with self.assertRaises(q.Blocked):
                self.queue().monitor()

    def test_nonunique_or_unapproved_edit_is_not_a_proposal(self):
        self.create()
        receipt = self.leased()
        for path, old in [
            ("b.cs", "=> 1"),
            ("../a.cs", "=> 1"),
            ("a.cs", "not present"),
            ("a.cs", ""),
        ]:
            proposal = self.proposal(receipt)
            proposal["edits"][0]["path"] = path
            proposal["edits"][0]["replacements"][0]["old"] = old
            with self.subTest(path=path, old=old), self.assertRaises(q.Blocked):
                self.queue().complete(proposal, execute=True, now=101)

    def test_execute_creates_bound_queue_and_refuses_overwrite(self):
        self.assertEqual(self.create()["status"], "created")
        self.assertTrue((self.state / "queue.sqlite3").is_file())
        with self.assertRaises(q.Blocked):
            self.create()

    def test_target_or_ancestor_reparse_marker_is_rejected(self):
        with patch.object(q, "is_reparse", return_value=True, create=True):
            with self.assertRaises(q.Blocked):
                q.plan(self.target, self.export, "feature/queue")

    def test_state_inside_target_or_traversal_is_rejected_even_dry_run(self):
        for state in (self.target / "queue", self.home / "other" / ".." / "queue"):
            with self.subTest(state=state), self.assertRaises(q.Blocked):
                q.slice_queue(self.target, self.export, state, "feature/queue")

    def test_revision_and_branch_mismatch_block_before_creation(self):
        for changes in ({"revision": "b" * 40}, {"branch": "other"}):
            with self.subTest(changes=changes), self.assertRaises(q.Blocked):
                q.slice_queue(
                    self.target,
                    self.export,
                    self.state,
                    "feature/queue",
                    execute=True,
                    identity_reader=lambda root, changes=changes: dict(
                        self.identity(root), **changes
                    ),
                )
        self.assertFalse(self.state.exists())
