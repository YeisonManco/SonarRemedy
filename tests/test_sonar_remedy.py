"""Tests for the SonarRemedy facade that drives the pipeline from config."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sonar_fetch
import sonar_hooks
import sonar_remedy
import sonar_remedy_config as rc


def _valid_config(**overrides):
    cfg = {
        "version": 1,
        "sonar": {
            "url": "https://sonar.example.com",
            "project_key": "my-project",
            "token_env": "SONAR_TOKEN",
        },
        "repository": {
            "url": "https://github.com/org/repo.git",
            "pat_env": "GIT_PAT",
            "local_path": "C:/work/repo",
            "main_branch": "main",
            "propagation_branches": [],
        },
        "worktrees": {"root": "C:/work/worktrees"},
        "provider": "opencode",
    }
    cfg.update(overrides)
    return cfg


class BuildFetchConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name

    def test_maps_sonar_and_repo(self):
        cfg = sonar_remedy.build_fetch_config(_valid_config(), self.repo)
        self.assertEqual(cfg.url, "https://sonar.example.com")
        self.assertEqual(cfg.project, "my-project")
        self.assertEqual(cfg.branch, "main")
        self.assertEqual(str(cfg.repo), os.path.realpath(self.repo))


class FetchCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        rc.save(_valid_config(), self.config_path)
        saved = os.environ.get("SONAR_TOKEN")
        self.addCleanup(lambda: self._restore("SONAR_TOKEN", saved))

    @staticmethod
    def _restore(name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def _run(self, argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return sonar_remedy.main(argv)

    def test_blocks_when_token_missing(self):
        os.environ.pop("SONAR_TOKEN", None)
        code = self._run(["--config", self.config_path, "fetch", "--repo", self.tmp.name])
        self.assertEqual(code, 2)

    def test_blocks_when_no_repo(self):
        cfg = _valid_config()
        del cfg["repository"]["local_path"]
        rc.save(cfg, self.config_path)
        os.environ["SONAR_TOKEN"] = "sqa_TEST"
        code = self._run(["--config", self.config_path, "fetch"])
        self.assertEqual(code, 2)

    def test_fetch_uses_config_and_token(self):
        os.environ["SONAR_TOKEN"] = "sqa_TEST"
        fake = {"status": "collected", "export": "x/export.json"}
        with mock.patch.object(sonar_fetch, "fetch", return_value=fake) as patched:
            code = self._run(["--config", self.config_path, "fetch", "--repo", self.tmp.name])
        self.assertEqual(code, 0)
        config_arg = patched.call_args[0][0]
        self.assertEqual(config_arg.project, "my-project")
        self.assertEqual(config_arg.branch, "main")
        self.assertEqual(config_arg.url, "https://sonar.example.com")


class SliceCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        rc.save(_valid_config(), self.config_path)

    def _run(self, argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return sonar_remedy.main(argv)

    def test_blocks_when_no_repo(self):
        cfg = _valid_config()
        del cfg["repository"]["local_path"]
        rc.save(cfg, self.config_path)
        code = self._run(
            [
                "--config",
                self.config_path,
                "slice",
                "--export",
                os.path.join(self.tmp.name, "export.json"),
                "--state",
                os.path.join(self.tmp.name, "q"),
            ]
        )
        self.assertEqual(code, 2)

    def test_slice_maps_config(self):
        import debt_queue

        fake = {"status": "dry-run", "binding": {}, "entries": 0, "jobs": 0}
        with mock.patch.object(debt_queue, "slice_queue", return_value=fake) as patched:
            code = self._run(
                [
                    "--config",
                    self.config_path,
                    "slice",
                    "--repo",
                    self.tmp.name,
                    "--export",
                    os.path.join(self.tmp.name, "export.json"),
                    "--state",
                    os.path.join(self.tmp.name, "q"),
                ]
            )
        self.assertEqual(code, 0)
        args = patched.call_args.args
        self.assertEqual(args[0], self.tmp.name)
        self.assertEqual(args[1], os.path.join(self.tmp.name, "export.json"))
        self.assertEqual(args[2], os.path.join(self.tmp.name, "q"))
        self.assertEqual(args[3], "main")
        self.assertIs(patched.call_args.kwargs.get("execute"), False)

    def test_slice_execute_flag(self):
        import debt_queue

        fake = {"status": "created"}
        with mock.patch.object(debt_queue, "slice_queue", return_value=fake) as patched:
            code = self._run(
                [
                    "--config",
                    self.config_path,
                    "slice",
                    "--repo",
                    self.tmp.name,
                    "--export",
                    os.path.join(self.tmp.name, "export.json"),
                    "--state",
                    os.path.join(self.tmp.name, "q"),
                    "--execute",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIs(patched.call_args.kwargs.get("execute"), True)


class RunCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        rc.save(_valid_config(), self.config_path)

    def _run(self, argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return sonar_remedy.main(argv)

    def test_run_maps_config_and_bounds(self):
        import debt_queue
        import debt_runner

        fake_work = mock.MagicMock()
        with (
            mock.patch.object(debt_queue, "Queue", return_value=fake_work) as qpatched,
            mock.patch.object(debt_runner, "run", return_value={"status": "dry-run"}) as rpatched,
        ):
            code = self._run(
                [
                    "--config",
                    self.config_path,
                    "run",
                    "--state",
                    os.path.join(self.tmp.name, "q"),
                    "--repo",
                    self.tmp.name,
                    "--limit",
                    "3",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(qpatched.call_args.args[0], os.path.join(self.tmp.name, "q"))
        self.assertEqual(qpatched.call_args.kwargs.get("target"), self.tmp.name)
        # The queue's binding is the source of truth for the branch; run does NOT
        # re-assert the config's main_branch (a stale config must not block it).
        self.assertIsNone(qpatched.call_args.kwargs.get("branch"))
        self.assertEqual(rpatched.call_args.args[0], fake_work)
        self.assertEqual(rpatched.call_args.kwargs.get("provider"), "manual")
        self.assertIs(rpatched.call_args.kwargs.get("execute"), False)
        self.assertEqual(rpatched.call_args.kwargs.get("limit"), 3)

    def test_run_passes_flags(self):
        import debt_queue
        import debt_runner

        fake_work = mock.MagicMock()
        with (
            mock.patch.object(debt_queue, "Queue", return_value=fake_work),
            mock.patch.object(
                debt_runner, "run", return_value={"status": "proposals_ready"}
            ) as rpatched,
        ):
            code = self._run(
                [
                    "--config",
                    self.config_path,
                    "run",
                    "--state",
                    os.path.join(self.tmp.name, "q"),
                    "--execute",
                    "--resume",
                    "--integrate",
                ]
            )
        self.assertEqual(code, 0)
        kwargs = rpatched.call_args.kwargs
        self.assertIs(kwargs.get("execute"), True)
        self.assertIs(kwargs.get("resume"), True)
        self.assertIs(kwargs.get("integrate"), True)


class ConfigureIntegrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Resolve the 8.3 short name some Windows runners expose in TEMP
        # (e.g. C:\Users\RUNNER~1) so hardened path checks see the canonical form.
        self.base = os.path.realpath(self.tmp.name)
        self.config_path = os.path.join(self.base, "config.json")
        rc.save(_valid_config(), self.config_path)
        self.checks_path = os.path.join(self.base, "checks.json")
        with open(self.checks_path, "w", encoding="utf-8") as handle:
            handle.write("{}")

    def _run(self, argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return sonar_remedy.main(argv)

    def test_configure_maps(self):
        import debt_executor
        import debt_queue

        fake_work = mock.MagicMock()
        with (
            mock.patch.object(debt_queue, "Queue", return_value=fake_work),
            mock.patch.object(
                debt_executor, "configure", return_value={"status": "configured"}
            ) as cpatched,
        ):
            code = self._run(
                [
                    "--config",
                    self.config_path,
                    "configure",
                    "--state",
                    os.path.join(self.base, "q"),
                    "--checks",
                    self.checks_path,
                    "--approve-checks-sha256",
                    "abc123",
                    "--execute",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(cpatched.call_args.args[0], fake_work)
        self.assertEqual(cpatched.call_args.args[1], {})
        self.assertEqual(cpatched.call_args.kwargs.get("approved_sha256"), "abc123")
        self.assertIs(cpatched.call_args.kwargs.get("execute"), True)

    def test_integrate_maps(self):
        import debt_executor
        import debt_queue

        fake_work = mock.MagicMock()
        with (
            mock.patch.object(debt_queue, "Queue", return_value=fake_work),
            mock.patch.object(
                debt_executor, "integrate", return_value={"status": "locally_verified"}
            ) as ipatched,
        ):
            code = self._run(
                [
                    "--config",
                    self.config_path,
                    "integrate",
                    "--state",
                    os.path.join(self.base, "q"),
                    "--job",
                    "jsomejobid",
                    "--execute",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(ipatched.call_args.args[0], fake_work)
        self.assertEqual(ipatched.call_args.args[1], "jsomejobid")
        self.assertIs(ipatched.call_args.kwargs.get("execute"), True)


class ProjectSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        rc.save(_valid_config(), self.config_path)

    def _run(self, argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return sonar_remedy.main(argv)

    @staticmethod
    def _restore_token(value):
        if value is None:
            os.environ.pop("SONAR_TOKEN", None)
        else:
            os.environ["SONAR_TOKEN"] = value

    def test_projects_list(self):
        with mock.patch.object(rc, "list_projects", return_value=["a", "b"]):
            code = self._run(["projects"])
        self.assertEqual(code, 0)

    def test_project_flag_loads_project_config(self):
        saved = os.environ.get("SONAR_TOKEN")
        self.addCleanup(lambda: self._restore_token(saved))
        os.environ["SONAR_TOKEN"] = "sqa_TEST"
        with (
            mock.patch.object(rc, "load_project", return_value=_valid_config()) as lpatched,
            mock.patch.object(sonar_fetch, "fetch", return_value={"status": "collected"}),
        ):
            code = self._run(["--project", "documentos", "fetch", "--repo", self.tmp.name])
        self.assertEqual(code, 0)
        lpatched.assert_called_once_with("documentos")

    def test_default_output_is_user_home(self):
        out = sonar_remedy._default_output("documentos")
        home = os.path.expanduser("~")
        self.assertTrue(out.startswith(os.path.join(home, ".sonar-remedy", "runs")))


class NextActionTests(unittest.TestCase):
    def test_run_batch_when_pending(self):
        self.assertEqual(sonar_remedy.next_action({"pending": 3}), "run_batch")

    def test_resume_when_leased(self):
        self.assertEqual(sonar_remedy.next_action({"leased": 1}), "resume")

    def test_integrate_when_proposed(self):
        self.assertEqual(sonar_remedy.next_action({"proposed": 2}), "integrate")

    def test_re_scan_when_exhausted(self):
        self.assertEqual(
            sonar_remedy.next_action({"applied": 5, "deferred": 2}), "re_scan_required"
        )

    def test_done_when_empty(self):
        self.assertEqual(sonar_remedy.next_action({}), "done")


class StatusCommandTests(unittest.TestCase):
    def test_status_reports_re_scan(self):
        import debt_queue

        fake_work = mock.MagicMock()
        fake_work.monitor.return_value = {
            "entry_states": {
                "pending": 0,
                "leased": 0,
                "proposed": 0,
                "applied": 2,
                "locally_verified": 0,
                "deferred": 1,
                "failed": 0,
                "sonar_confirmed": 0,
                "unavailable": 0,
            }
        }
        buf = io.StringIO()
        with mock.patch.object(debt_queue, "Queue", return_value=fake_work):
            with contextlib.redirect_stdout(buf):
                code = sonar_remedy.main(["status", "--state", "C:/q"])
        self.assertEqual(code, 0)
        self.assertIn('"re_scan_required"', buf.getvalue())

    def test_status_invalid_state_reports_real_reason(self):
        import debt_queue

        buf = io.StringIO()
        with mock.patch.object(
            debt_queue, "Queue", side_effect=debt_queue.Blocked("state_must_be_outside_target")
        ):
            with contextlib.redirect_stdout(buf):
                code = sonar_remedy.main(["status", "--state", "C:/q"])
        self.assertEqual(code, 2)
        self.assertIn("state_must_be_outside_target", buf.getvalue())
        self.assertNotIn('"Blocked"', buf.getvalue())


class AnalyzeCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        rc.save(_valid_config(), self.config_path)

    @staticmethod
    def _restore_token(value):
        if value is None:
            os.environ.pop("SONAR_TOKEN", None)
        else:
            os.environ["SONAR_TOKEN"] = value

    def test_analyze_argv_maps_config(self):
        argv = sonar_remedy._analyze_argv(_valid_config(), "C:/pipeline.ps1", "C:/repo", "main")
        self.assertEqual(argv[0], "pwsh")
        self.assertIn("-WorktreePath", argv)
        self.assertIn("C:/repo", argv)
        self.assertIn("-BranchName", argv)
        self.assertIn("main", argv)
        self.assertIn("-ProjectKey", argv)
        self.assertIn("my-project", argv)
        self.assertIn("-SonarUrl", argv)
        self.assertIn("https://sonar.example.com", argv)

    def test_analyze_argv_skip_pull(self):
        argv = sonar_remedy._analyze_argv(_valid_config(), "s.ps1", "r", "main", skip_pull=True)
        self.assertIn("-SkipPull", argv)

    def test_analyze_defaults_to_builtin_script(self):
        saved = os.environ.get("SONAR_TOKEN")
        self.addCleanup(lambda: self._restore_token(saved))
        os.environ["SONAR_TOKEN"] = "sqa_TEST"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = sonar_remedy.main(
                ["--config", self.config_path, "analyze", "--repo", self.tmp.name]
            )
        self.assertEqual(code, 0)
        self.assertIn("sonar_compact.ps1", buf.getvalue())

    def test_analyze_dry_run_prints_argv(self):
        saved = os.environ.get("SONAR_TOKEN")
        self.addCleanup(lambda: self._restore_token(saved))
        os.environ["SONAR_TOKEN"] = "sqa_TEST"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = sonar_remedy.main(
                [
                    "--config",
                    self.config_path,
                    "analyze",
                    "--script",
                    "C:/pipeline.ps1",
                    "--repo",
                    self.tmp.name,
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn('"dry-run"', buf.getvalue())

    def test_analyze_blocks_when_token_missing(self):
        os.environ.pop("SONAR_TOKEN", None)
        with contextlib.redirect_stdout(io.StringIO()):
            code = sonar_remedy.main(
                [
                    "--config",
                    self.config_path,
                    "analyze",
                    "--script",
                    "C:/pipeline.ps1",
                    "--repo",
                    self.tmp.name,
                ]
            )
        self.assertEqual(code, 2)


class EstimateTests(unittest.TestCase):
    def test_remaining_and_eta(self):
        states = {
            "pending": 10,
            "leased": 2,
            "proposed": 3,
            "applied": 20,
            "locally_verified": 5,
            "deferred": 4,
            "failed": 1,
        }
        est = sonar_remedy.estimate(states, minutes_per_job=10)
        self.assertEqual(est["remaining_jobs"], 15)
        self.assertEqual(est["resolved_jobs"], 30)
        self.assertEqual(est["total_jobs"], 45)
        self.assertEqual(est["estimated_minutes"], 150)
        self.assertEqual(est["estimated_hours"], 2.5)

    def test_empty(self):
        est = sonar_remedy.estimate({})
        self.assertEqual(est["remaining_jobs"], 0)
        self.assertEqual(est["estimated_hours"], 0.0)


class ProgressCommandTests(unittest.TestCase):
    def test_progress_writes_file(self):
        import debt_queue

        with tempfile.TemporaryDirectory() as d:
            fake_work = mock.MagicMock()
            fake_work.monitor.return_value = {
                "entry_states": {
                    "pending": 4,
                    "leased": 0,
                    "proposed": 0,
                    "applied": 0,
                    "locally_verified": 0,
                    "deferred": 0,
                    "failed": 0,
                }
            }
            with mock.patch.object(debt_queue, "Queue", return_value=fake_work):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = sonar_remedy.main(["progress", "--state", os.path.join(d, "q")])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(os.path.join(d, "q", "progress.md")))


class SchedulePlanTests(unittest.TestCase):
    def test_groups_and_classifies(self):
        jobs = [
            {
                "job_id": "j1",
                "path": "a.py",
                "kind": "coverage",
                "write_paths": ["a.py"],
                "issue_count": 1,
            },
            {
                "job_id": "j2",
                "path": "b.py",
                "kind": "smells",
                "write_paths": ["b.py"],
                "issue_count": 1,
            },
            {
                "job_id": "j3",
                "path": "b.py",
                "kind": "smells",
                "write_paths": ["b.py"],
                "issue_count": 1,
            },
            {
                "job_id": "j4",
                "path": "c.py",
                "kind": "coverage",
                "write_paths": ["c.py"],
                "issue_count": 1,
            },
        ]
        plan = sonar_remedy.schedule_plan(jobs)
        self.assertEqual(plan["jobs"], 4)
        self.assertEqual(plan["files"], 3)
        self.assertEqual(plan["parallel_files"], 2)
        self.assertEqual(plan["serial_files"], 1)
        self.assertEqual(plan["refactor_files"], 1)
        self.assertEqual(plan["recommended_workers"], 2)

    def test_empty(self):
        plan = sonar_remedy.schedule_plan([])
        self.assertEqual(plan["jobs"], 0)
        self.assertEqual(plan["recommended_workers"], 0)


class ScheduleCommandTests(unittest.TestCase):
    def test_schedule_reports_plan(self):
        import debt_queue

        fake_work = mock.MagicMock()
        fake_work.pending.return_value = [
            {
                "job_id": "j1",
                "path": "a.py",
                "kind": "coverage",
                "write_paths": ["a.py"],
                "issue_count": 1,
            },
        ]
        buf = io.StringIO()
        with mock.patch.object(debt_queue, "Queue", return_value=fake_work):
            with contextlib.redirect_stdout(buf):
                code = sonar_remedy.main(["schedule", "--state", "C:/q"])
        self.assertEqual(code, 0)
        self.assertIn('"parallel_files"', buf.getvalue())


class HintTests(unittest.TestCase):
    def test_each_kind_has_a_hint(self):
        for kind in ("security", "hotspots", "smells", "coverage", "duplication"):
            self.assertTrue(sonar_remedy.hint_for(kind))

    def test_unknown_kind_empty(self):
        self.assertEqual(sonar_remedy.hint_for("unknown"), "")

    def test_hint_is_short(self):
        # Distilled guidance must stay tiny (token-efficient), not a full skill.
        for kind in ("security", "hotspots", "smells", "coverage", "duplication"):
            self.assertLess(len(sonar_remedy.hint_for(kind)), 600)


class LanguageHintTests(unittest.TestCase):
    def test_language_for_detects_extension(self):
        cases = {
            "src/a.py": "python",
            "src/B.cs": "csharp",
            "src/app.ts": "typescript",
            "src/App.tsx": "typescript",
            "src/App.jsx": "javascript",
            "src/B.java": "java",
            "src/main.go": "go",
            "src/lib.rs": "rust",
        }
        for path, expected in cases.items():
            self.assertEqual(sonar_remedy.language_for(path), expected)

    def test_language_for_unknown_is_empty(self):
        self.assertEqual(sonar_remedy.language_for("README.md"), "")

    def test_language_hint_covers_key_languages(self):
        # Angular/React/.NET/Python must all have distilled guidance.
        for path in ("x.py", "x.cs", "x.ts", "x.jsx"):
            self.assertTrue(sonar_remedy.language_hint_for(path))

    def test_language_hint_is_short(self):
        for path in ("x.py", "x.cs", "x.ts", "x.jsx"):
            self.assertLess(len(sonar_remedy.language_hint_for(path)), 600)

    def test_language_hint_unknown_empty(self):
        self.assertEqual(sonar_remedy.language_hint_for("README.md"), "")


class RunAllCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        rc.save(_valid_config(), self.config_path)
        saved = os.environ.get("SONAR_TOKEN")
        self.addCleanup(lambda: self._restore("SONAR_TOKEN", saved))

    @staticmethod
    def _restore(name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def test_run_all_slices_each_chunk(self):
        import debt_queue

        os.environ["SONAR_TOKEN"] = "sqa_TEST"
        fake = {
            "status": "collected",
            "export": "x/export.json",
            "exports": ["x/chunk-0.json", "x/chunk-1.json"],
            "chunks": 2,
            "issues_total": 4000,
            "gate": "ERROR",
        }
        with (
            mock.patch.object(sonar_fetch, "fetch", return_value=fake),
            mock.patch.object(
                debt_queue, "slice_queue", return_value={"status": "created"}
            ) as spatched,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = sonar_remedy.main(
                [
                    "--config",
                    self.config_path,
                    "run-all",
                    "--repo",
                    self.tmp.name,
                    "--state",
                    os.path.join(self.tmp.name, "q"),
                    "--execute",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(spatched.call_count, 2)
        states = {call.args[2] for call in spatched.call_args_list}
        self.assertEqual(len(states), 2)

    def test_run_all_blocks_when_token_missing(self):
        os.environ.pop("SONAR_TOKEN", None)
        with contextlib.redirect_stdout(io.StringIO()):
            code = sonar_remedy.main(
                [
                    "--config",
                    self.config_path,
                    "run-all",
                    "--repo",
                    self.tmp.name,
                    "--state",
                    os.path.join(self.tmp.name, "q"),
                ]
            )
        self.assertEqual(code, 2)

    def test_facade_reports_os_error_detail(self):
        with mock.patch.object(
            sonar_remedy, "_write_init_files", side_effect=PermissionError("denied: probe")
        ):
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                code = sonar_remedy.main(["init", "--dir", self.tmp.name])
        self.assertEqual(code, 2)
        output = buf.getvalue()
        self.assertIn("PermissionError", output)
        self.assertIn("denied: probe", output)


class DetectChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "target")
        os.makedirs(os.path.join(self.repo, "tests"))
        with open(os.path.join(self.repo, "app.sln"), "w", encoding="utf-8") as fh:
            fh.write("sln\n")
        with open(
            os.path.join(self.repo, "tests", "App.Tests.csproj"), "w", encoding="utf-8"
        ) as fh:
            fh.write("csproj\n")
        self.bindir = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bindir)
        self.exe = os.path.join(self.bindir, "dotnet.exe")
        with open(self.exe, "wb") as fh:
            fh.write(b"fake-dotnet")
        self.path = self.bindir + os.pathsep + os.environ.get("PATH", "")

    def _run_cli(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = sonar_remedy.main(argv)
        return code, buf.getvalue()

    def test_detects_dotnet_solution_tests_and_exe(self):
        import hashlib

        with mock.patch.dict(os.environ, {"PATH": self.path}):
            result = sonar_remedy.detect_checks(self.repo)
        self.assertEqual(result["status"], "detected")
        draft = result["draft"]
        self.assertIn("app.sln", draft["checks"][0]["argv"])
        # which() returns the PATHEXT casing of the machine (dotnet.EXE vs
        # dotnet.exe), and the fixture path itself may be aliased: the draft
        # must carry the canonical form of the same file.
        import debt_queue

        self.assertEqual(
            os.path.normcase(draft["checks"][0]["argv"][0]),
            os.path.normcase(str(debt_queue.canonical_case(self.exe))),
        )
        self.assertEqual(
            draft["checks"][0]["executable_sha256"],
            hashlib.sha256(b"fake-dotnet").hexdigest(),
        )
        self.assertIn("tests/App.Tests.csproj", draft["test_paths"])
        self.assertIn("HUMAN", draft["characterization_reason"])
        self.assertTrue(result["missing"])
        # Every absolute path the draft emits must satisfy the validator
        # itself (no case_alias on its own output, on any machine).
        import debt_queue

        for check in draft["checks"]:
            debt_queue.local_path(check["argv"][0], exists=True)

    def test_missing_dotnet_without_inventing_paths(self):
        with mock.patch.dict(os.environ, {"PATH": self.tmp.name}, clear=False):
            with mock.patch.object(sonar_remedy.shutil, "which", return_value=None):
                result = sonar_remedy.detect_checks(self.repo)
        self.assertEqual(result["status"], "missing")
        self.assertIsNone(result["draft"])
        self.assertTrue(any("dotnet" in item for item in result["missing"]))

    def test_ambiguous_solutions_ask_human(self):
        with open(os.path.join(self.repo, "other.sln"), "w", encoding="utf-8") as fh:
            fh.write("sln\n")
        with mock.patch.dict(os.environ, {"PATH": self.path}):
            result = sonar_remedy.detect_checks(self.repo)
        self.assertEqual(result["status"], "missing")
        self.assertTrue(
            any("app.sln" in item and "other.sln" in item for item in result["missing"])
        )

    def test_node_scripts_are_reported_not_wired(self):
        import json

        with open(os.path.join(self.repo, "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"scripts": {"test": "jest", "build": "ng build"}}, fh)
        with mock.patch.dict(os.environ, {"PATH": self.path}):
            result = sonar_remedy.detect_checks(self.repo)
        scripts = result["detected"].get("node_scripts", {})
        self.assertEqual(scripts.get("test"), "jest")
        self.assertEqual(scripts.get("build"), "ng build")

    def test_cli_prints_draft(self):
        with mock.patch.dict(os.environ, {"PATH": self.path}):
            code, output = self._run_cli(["detect-checks", "--repo", self.repo])
        self.assertEqual(code, 0)
        self.assertIn("draft", output)


class HookTests(unittest.TestCase):
    HOOK_VERSION = 1

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.proj)
        self.gitproj = os.path.join(self.tmp.name, "gitproj")
        os.makedirs(os.path.join(self.gitproj, ".git", "hooks"))

    def _hook_path(self, root):
        return os.path.join(root, ".git", "hooks", "pre-push")

    def test_install_skips_non_repo(self):
        result = sonar_hooks.install(self.proj)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("not_a_git_checkout", result["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".git")))

    def test_install_creates_hook_in_checkout(self):
        result = sonar_hooks.install(self.gitproj)
        self.assertEqual(result["status"], "installed")
        with open(self._hook_path(self.gitproj), encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("sonar_hooks.py", content)
        self.assertIn("SonarRemedy", content)

    def test_install_is_idempotent(self):
        sonar_hooks.install(self.gitproj)
        with open(self._hook_path(self.gitproj), encoding="utf-8") as fh:
            first = fh.read()
        result = sonar_hooks.install(self.gitproj)
        self.assertEqual(result["status"], "ok")
        with open(self._hook_path(self.gitproj), encoding="utf-8") as fh:
            second = fh.read()
        self.assertEqual(first, second)
        # The pack path itself may contain the marker word (e.g. a
        # `SonarRemedy` checkout folder), so count the marker, not the word.
        self.assertEqual(second.count(sonar_hooks.MARKER), 1)

    def test_install_refuses_foreign_hook(self):
        with open(self._hook_path(self.gitproj), "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho foreign\n")
        with self.assertRaises(Exception) as ctx:
            sonar_hooks.install(self.gitproj)
        self.assertEqual(type(ctx.exception).__name__, "Blocked")
        self.assertIn("foreign_hook", str(ctx.exception))
        with open(self._hook_path(self.gitproj), encoding="utf-8") as fh:
            self.assertIn("foreign", fh.read())

    def test_status_reports_missing_ok_and_outdated(self):
        self.assertEqual(sonar_hooks.status(self.proj)["status"], "not_a_repo")
        self.assertEqual(sonar_hooks.status(self.gitproj)["status"], "missing")
        sonar_hooks.install(self.gitproj)
        self.assertEqual(sonar_hooks.status(self.gitproj)["status"], "ok")
        with open(self._hook_path(self.gitproj), "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n# SonarRemedy pre-push gate v0\n")
        self.assertEqual(sonar_hooks.status(self.gitproj)["status"], "outdated")

    def test_run_gates_passes_declared_commands(self):
        hooks = {"pre-push": [[__import__("sys").executable, "-B", "-c", "pass"]]}
        with open(os.path.join(self.proj, ".sonarremedy-hooks.json"), "w", encoding="utf-8") as fh:
            __import__("json").dump(hooks, fh)
        result = sonar_hooks.run_gates(self.proj)
        self.assertEqual(result["status"], "ok")

    def test_run_gates_blocks_on_failing_command(self):
        hooks = {
            "pre-push": [[__import__("sys").executable, "-B", "-c", "import sys; sys.exit(3)"]]
        }
        with open(os.path.join(self.proj, ".sonarremedy-hooks.json"), "w", encoding="utf-8") as fh:
            __import__("json").dump(hooks, fh)
        result = sonar_hooks.run_gates(self.proj)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("returncode", result["reason"])

    def test_run_gates_blocks_without_gates_in_managed_project(self):
        os.makedirs(os.path.join(self.proj, ".sonarremedy"))
        result = sonar_hooks.run_gates(self.proj)
        self.assertEqual(result["status"], "blocked")
        self.assertIn(".sonarremedy-hooks.json", result["fix"])

    def test_run_gates_pack_markers_mirror_ci(self):
        os.makedirs(os.path.join(self.proj, "tests"))
        with open(os.path.join(self.proj, "sonar_remedy.py"), "w", encoding="utf-8") as fh:
            fh.write('"""Marker."""\n')
        with mock.patch.object(sonar_hooks, "_has_ruff", return_value=False):
            result = sonar_hooks.run_gates(self.proj)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("ruff", result["reason"])

    def test_init_installs_hook_in_checkout(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = sonar_remedy.main(["init", "--dir", self.gitproj])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(self._hook_path(self.gitproj)))

    def test_doctor_reports_and_fixes_missing_hook(self):
        with contextlib.redirect_stdout(io.StringIO()):
            sonar_remedy.main(["init", "--dir", self.gitproj])
        os.remove(self._hook_path(self.gitproj))
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = sonar_remedy.main(["doctor", "--dir", self.gitproj])
        self.assertEqual(code, 0)
        self.assertIn("git_hooks", buf.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = sonar_remedy.main(["doctor", "--fix", "--dir", self.gitproj])
        self.assertEqual(code, 0)
        self.assertIn("git_hooks", buf.getvalue())
        self.assertTrue(os.path.isfile(self._hook_path(self.gitproj)))


class AutopilotCommandTests(unittest.TestCase):
    REVISION = "b" * 40

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "target")
        os.makedirs(self.repo)
        with open(os.path.join(self.repo, "a.cs"), "w", encoding="utf-8") as fh:
            fh.write("class A { int Value() => 1; }\n")
        self.state = os.path.join(self.tmp.name, "queue")
        self.export = os.path.join(self.tmp.name, "export.json")
        self.config_path = os.path.join(self.tmp.name, "config.json")
        rc.save(_valid_config(), self.config_path)

    def _identity(self, root):
        return {"root": str(root), "branch": "main", "revision": self.REVISION}

    def _write_export(self, entries):
        with open(self.export, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "revision": self.REVISION, "issues": entries}, fh)

    def _issue(self, name="S1"):
        return {"id": name, "path": "a.cs", "kind": "smells", "line": 1, "rule": "csharp:S1"}

    def _init_repo(self):
        with contextlib.redirect_stdout(io.StringIO()):
            sonar_remedy.main(["init", "--dir", self.repo])

    def _factory(self, context):
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
            "test_plan": "Autopilot fixture only; no model runs tests.",
            "edits": [
                {
                    "path": source["path"],
                    "before_sha256": source["sha256"],
                    "replacements": [{"old": "Value()", "new": "Value( )"}],
                }
            ],
        }

    def _run_cli(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = sonar_remedy.main(argv)
        return code, buf.getvalue()

    def test_autopilot_dry_run_plans_phases_without_writing(self):
        result = sonar_remedy.autopilot(self.repo, self.state)
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(
            result["phases"], ["setup", "slice", "run", "configure", "integrate", "status"]
        )
        self.assertFalse(os.path.exists(self.state))

    def test_autopilot_blocks_when_setup_missing(self):
        self._write_export([self._issue()])
        result = sonar_remedy.autopilot(
            self.repo,
            self.state,
            export=self.export,
            execute=True,
            identity_reader=self._identity,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["phase"], "setup")
        self.assertIn("init --dir", result["fix"])
        self.assertFalse(os.path.exists(self.state))

    def test_autopilot_blocks_when_no_queue_and_no_export(self):
        self._init_repo()
        result = sonar_remedy.autopilot(
            self.repo, self.state, execute=True, identity_reader=self._identity
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["phase"], "slice")
        self.assertIn("--export", result["fix"])

    def test_autopilot_surfaces_awaiting_proposals(self):
        self._init_repo()
        self._write_export([self._issue()])
        first = sonar_remedy.autopilot(
            self.repo,
            self.state,
            export=self.export,
            execute=True,
            identity_reader=self._identity,
        )
        self.assertEqual(first["status"], "awaiting_proposals")
        waiting = first["waiting"]
        self.assertEqual(len(waiting), 1)
        self.assertTrue(waiting[0]["proposal_path"].endswith(".json"))
        second = sonar_remedy.autopilot(
            self.repo, self.state, execute=True, identity_reader=self._identity
        )
        self.assertEqual(second["status"], "awaiting_proposals")
        self.assertEqual(second["waiting"][0]["attempt_id"], waiting[0]["attempt_id"])

    def test_autopilot_proposals_ready_via_factory(self):
        self._init_repo()
        self._write_export([self._issue()])
        result = sonar_remedy.autopilot(
            self.repo,
            self.state,
            export=self.export,
            execute=True,
            identity_reader=self._identity,
            proposal_factory=self._factory,
        )
        self.assertEqual(result["status"], "proposals_ready")
        self.assertEqual(len(result["jobs"]), 1)
        self.assertIn("configure", result["next_command"])

    def test_autopilot_integrate_blocked_without_executor_config(self):
        self._init_repo()
        self._write_export([self._issue()])
        result = sonar_remedy.autopilot(
            self.repo,
            self.state,
            export=self.export,
            execute=True,
            integrate=True,
            identity_reader=self._identity,
            proposal_factory=self._factory,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["phase"], "configure")
        self.assertIn("--approve-checks-sha256", result["fix"])

    def test_autopilot_rejects_bad_bounds(self):
        with self.assertRaises(Exception) as ctx:
            sonar_remedy.autopilot(self.repo, self.state, limit=0)
        self.assertEqual(type(ctx.exception).__name__, "Blocked")

    def test_autopilot_cli_dry_run(self):
        code, output = self._run_cli(
            [
                "--config",
                self.config_path,
                "autopilot",
                "--state",
                os.path.join(self.tmp.name, "q"),
                "--repo",
                self.tmp.name,
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("dry-run", output)
        self.assertIn("setup", output)


class ConfigureProjectCommandTests(unittest.TestCase):
    def test_configure_project_saves_config(self):
        with mock.patch.object(rc, "save_project") as spatched:
            with contextlib.redirect_stdout(io.StringIO()):
                code = sonar_remedy.main(
                    [
                        "configure-project",
                        "--name",
                        "mem",
                        "--sonar-url",
                        "http://h:9000",
                        "--project-key",
                        "PK",
                        "--repo-url",
                        "https://github.com/o/r",
                        "--local-path",
                        "C:/r",
                        "--worktree-root",
                        "C:/wt",
                        "--main-branch",
                        "feature/Sonar",
                        "--allow-http",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertEqual(spatched.call_args.args[0], "mem")
        cfg = spatched.call_args.args[1]
        self.assertEqual(cfg["sonar"]["url"], "http://h:9000")
        self.assertTrue(cfg["sonar"]["allow_http"])
        self.assertEqual(cfg["sonar"]["token_env"], "SONAR_TOKEN")
        self.assertEqual(cfg["repository"]["main_branch"], "feature/Sonar")

    def test_configure_project_detects_branch_from_url(self):
        with mock.patch.object(rc, "save_project") as spatched:
            with contextlib.redirect_stdout(io.StringIO()):
                code = sonar_remedy.main(
                    [
                        "configure-project",
                        "--name",
                        "mem",
                        "--sonar-url",
                        "https://h/dashboard?id=PK&branch=feature/Sonar",
                        "--project-key",
                        "PK",
                        "--repo-url",
                        "https://github.com/o/r",
                        "--local-path",
                        "C:/r",
                        "--worktree-root",
                        "C:/wt",
                    ]
                )
        self.assertEqual(code, 0)
        cfg = spatched.call_args.args[1]
        self.assertEqual(cfg["repository"]["main_branch"], "feature/Sonar")

    def test_configure_project_custom_env_names(self):
        with mock.patch.object(rc, "save_project") as spatched:
            with contextlib.redirect_stdout(io.StringIO()):
                code = sonar_remedy.main(
                    [
                        "configure-project",
                        "--name",
                        "mem",
                        "--sonar-url",
                        "https://h",
                        "--project-key",
                        "PK",
                        "--repo-url",
                        "https://github.com/o/r",
                        "--local-path",
                        "C:/r",
                        "--worktree-root",
                        "C:/wt",
                        "--token-env",
                        "SONAR_TOKEN_PROJECT_B",
                        "--pat-env",
                        "GIT_PAT_PROJECT_B",
                    ]
                )
        self.assertEqual(code, 0)
        cfg = spatched.call_args.args[1]
        self.assertEqual(cfg["sonar"]["token_env"], "SONAR_TOKEN_PROJECT_B")
        self.assertEqual(cfg["repository"]["pat_env"], "GIT_PAT_PROJECT_B")


class ConfigureProjectsCommandTests(unittest.TestCase):
    def test_configure_projects_loops_and_saves(self):
        inputs = iter(
            [
                # projA
                "projA",
                "https://sonar.a.com",
                "key-a",
                "",
                "https://github.com/o/a",
                "C:/wt-a",
                "",
                # projB
                "projB",
                "https://sonar.b.com",
                "key-b",
                "SONAR_TOKEN_B",
                "https://github.com/o/b",
                "C:/wt-b",
                "",
                # finish
                "",
            ]
        )
        with mock.patch("builtins.input", side_effect=lambda *_: next(inputs)):
            with mock.patch.object(rc, "save_project") as spatched:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = sonar_remedy.main(["configure-projects"])
        self.assertEqual(code, 0)
        self.assertEqual([c.args[0] for c in spatched.call_args_list], ["projA", "projB"])
        self.assertEqual(spatched.call_args_list[0].args[1]["sonar"]["token_env"], "SONAR_TOKEN")
        self.assertEqual(spatched.call_args_list[1].args[1]["sonar"]["token_env"], "SONAR_TOKEN_B")


class UpdateCommandTests(unittest.TestCase):
    def test_update_blocks_when_not_a_git_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                code = sonar_remedy.main(["update", "--path", d])
        self.assertEqual(code, 2)

    def test_update_pulls_and_reinstalls(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            with (
                mock.patch("subprocess.run") as rpatched,
                mock.patch("subprocess.Popen"),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = sonar_remedy.main(["update", "--path", d])
        self.assertEqual(code, 0)
        self.assertEqual(rpatched.call_count, 1)

    def test_update_auto_detects_module_dir(self):
        module_dir = os.path.dirname(os.path.abspath(sonar_remedy.__file__))
        with (
            mock.patch("subprocess.run") as rpatched,
            mock.patch("subprocess.Popen"),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                code = sonar_remedy.main(["update"])
        self.assertEqual(code, 0)
        self.assertEqual(rpatched.call_args.args[0], ["git", "-C", module_dir, "pull"])


class InitCommandTests(unittest.TestCase):
    def test_init_writes_mcp_and_instructions(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                code = sonar_remedy.main(["init", "--dir", d])
            self.assertEqual(code, 0)
            mcp_path = os.path.join(d, ".vscode", "mcp.json")
            instr_path = os.path.join(d, ".github", "copilot-instructions.md")
            full_path = os.path.join(d, ".github", "sonarremedy-instructions.md")
            self.assertTrue(os.path.isfile(mcp_path))
            self.assertTrue(os.path.isfile(instr_path))
            self.assertTrue(os.path.isfile(full_path))
            with open(mcp_path, encoding="utf-8") as fh:
                mcp_content = fh.read()
            self.assertIn("sonar-remedy", mcp_content)
            self.assertIn("sonar_remedy_mcp.py", mcp_content)
            with open(instr_path, encoding="utf-8") as fh:
                instr_content = fh.read()
            self.assertIn("sonar_remedy_", instr_content)
            self.assertIn("instructions.md", instr_content)  # the slim pointer
            with open(full_path, encoding="utf-8") as fh:
                full_content = fh.read()
            self.assertIn("Always use SonarRemedy", full_content)

    def test_init_preserves_existing_copilot_instructions(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".github"), exist_ok=True)
            user_content = "# My team rules\n\n- Always use tabs\n- No console.log\n"
            instructions = os.path.join(d, ".github", "copilot-instructions.md")
            with open(instructions, "w", encoding="utf-8") as fh:
                fh.write(user_content)
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            with open(instructions, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("My team rules", content)
            self.assertIn("Always use tabs", content)
            self.assertIn("SonarRemedy:start", content)
            self.assertIn("sonar_remedy_", content)
            # Re-init replaces only our section, never duplicates or destroys user content.
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            with open(instructions, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("My team rules", content)
            self.assertEqual(content.count("SonarRemedy:start"), 1)

    def test_init_writes_version_marker(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            version_file = os.path.join(d, ".sonarremedy", "version.json")
            self.assertTrue(os.path.isfile(version_file))
            with open(version_file, encoding="utf-8") as fh:
                self.assertIn("version", fh.read())

    def test_check_reports_not_initialized_then_up_to_date(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                code = sonar_remedy.main(["check", "--dir", d])
            self.assertEqual(code, 0)
            self.assertIn("not_initialized", buf.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                code = sonar_remedy.main(["check", "--dir", d])
            self.assertEqual(code, 0)
            self.assertIn("up_to_date", buf.getvalue())

    def test_doctor_reports_version_check(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                code = sonar_remedy.main(["doctor", "--dir", d])
            self.assertEqual(code, 0)
            output = buf.getvalue()
            self.assertIn("version", output)
            self.assertIn("not initialized", output)

    def test_doctor_reports_and_fixes_orphaned_barrier(self):
        import debt_executor
        import debt_queue

        with tempfile.TemporaryDirectory() as home:
            target = str(debt_queue.canonical_case(os.path.join(home, "target")))
            os.makedirs(target)
            with open(os.path.join(target, "a.cs"), "w", encoding="utf-8") as fh:
                fh.write("class A {}\n")
            before = debt_executor.snapshot(target)
            control = os.path.join(home, "control")
            folder = os.path.join(
                control, debt_queue.digest(os.path.abspath(target).casefold().encode())
            )
            os.makedirs(folder)
            intent_dir = os.path.join(home, "state", "jobs", "j1", "attempts", "a1", "integration")
            os.makedirs(intent_dir)
            with open(os.path.join(intent_dir, "intent.json"), "w", encoding="utf-8") as fh:
                fh.write(
                    debt_queue.encoded(
                        {"job_id": "j1", "before": before, "write_paths": ["a.cs"]}
                    ).decode()
                )
            with open(os.path.join(folder, "active.json"), "w", encoding="utf-8") as fh:
                fh.write(
                    debt_queue.encoded(
                        {"job_id": "j1", "intent": os.path.join(intent_dir, "intent.json")}
                    ).decode()
                )
            with mock.patch.object(debt_executor, "CONTROL_ROOT", Path(control)):
                with contextlib.redirect_stdout(io.StringIO()) as buf:
                    code = sonar_remedy.main(["doctor", "--repo", target, "--dir", home])
                self.assertEqual(code, 0)
                self.assertIn("barrier", buf.getvalue())
                self.assertIn("orphaned", buf.getvalue())
                with contextlib.redirect_stdout(io.StringIO()):
                    code = sonar_remedy.main(["doctor", "--repo", target, "--dir", home, "--fix"])
                self.assertEqual(code, 0)
                self.assertFalse(os.path.isfile(os.path.join(folder, "active.json")))

    def test_doctor_fix_reinitializes_outdated_project(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".sonarremedy"), exist_ok=True)
            version_file = os.path.join(d, ".sonarremedy", "version.json")
            with open(version_file, "w", encoding="utf-8") as fh:
                fh.write('{"version": "0.0.1"}\n')
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                code = sonar_remedy.main(["doctor", "--fix", "--dir", d])
            self.assertEqual(code, 0)
            self.assertIn("re-ran init", buf.getvalue())
            with open(version_file, encoding="utf-8") as fh:
                self.assertIn(sonar_remedy.__version__, fh.read())

    def test_check_reports_not_initialized_when_init_files_missing(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            os.remove(os.path.join(d, ".github", "copilot-instructions.md"))
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                code = sonar_remedy.main(["check", "--dir", d])
            self.assertEqual(code, 0)
            self.assertIn("not_initialized", buf.getvalue())
            self.assertIn("copilot-instructions.md", buf.getvalue())

    def test_doctor_reports_missing_init_files(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            os.remove(os.path.join(d, ".vscode", "mcp.json"))
            os.remove(os.path.join(d, ".github", "sonarremedy-instructions.md"))
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                code = sonar_remedy.main(["doctor", "--dir", d])
            self.assertEqual(code, 0)
            output = buf.getvalue()
            self.assertIn("init_files", output)
            self.assertIn("init", output)

    def test_doctor_fix_recreates_missing_init_files(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            rules = os.path.join(d, ".sonarremedy", "rules.json")
            with open(rules, "w", encoding="utf-8") as fh:
                fh.write('{"whitelist": ["WORKTREE.KEEP"], "blacklist": []}\n')
            os.remove(os.path.join(d, ".github", "copilot-instructions.md"))
            os.remove(os.path.join(d, ".github", "sonarremedy-instructions.md"))
            os.remove(os.path.join(d, ".vscode", "mcp.json"))
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                code = sonar_remedy.main(["doctor", "--fix", "--dir", d])
            self.assertEqual(code, 0)
            self.assertIn("re-ran init", buf.getvalue())
            self.assertTrue(os.path.isfile(os.path.join(d, ".github", "copilot-instructions.md")))
            self.assertTrue(
                os.path.isfile(os.path.join(d, ".github", "sonarremedy-instructions.md"))
            )
            self.assertTrue(os.path.isfile(os.path.join(d, ".vscode", "mcp.json")))
            with open(rules, encoding="utf-8") as fh:
                self.assertIn("WORKTREE.KEEP", fh.read())

    def test_init_creates_sonarremedy_dir_and_rules(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            base = os.path.join(d, ".sonarremedy")
            self.assertTrue(os.path.isfile(os.path.join(base, "rules.json")))
            for sub in ("queues", "runs", "temp"):
                self.assertTrue(os.path.isdir(os.path.join(base, sub)))
            with open(os.path.join(d, ".gitignore"), encoding="utf-8") as fh:
                gitignore = fh.read()
            self.assertIn(".sonarremedy/", gitignore)
            self.assertIn(".vscode/mcp.json", gitignore)
            # .github/copilot-instructions.md is the user's own file — never gitignored.
            self.assertNotIn(".github/copilot-instructions.md", gitignore)

    def test_init_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            rules = os.path.join(d, ".sonarremedy", "rules.json")
            with open(rules, "w", encoding="utf-8") as fh:
                fh.write('{"whitelist": ["ANGULAR.TS_IGNORE"], "blacklist": []}\n')
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            with open(rules, encoding="utf-8") as fh:
                self.assertIn("ANGULAR.TS_IGNORE", fh.read())

    def test_clean_keeps_rules_and_reset_removes_all(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", d])
            generated = os.path.join(d, ".sonarremedy", "queues", "q.sqlite")
            with open(generated, "w", encoding="utf-8") as fh:
                fh.write("x")
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["clean", "--dir", d])
            self.assertTrue(os.path.isfile(os.path.join(d, ".sonarremedy", "rules.json")))
            self.assertFalse(os.path.isdir(os.path.join(d, ".sonarremedy", "queues")))
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["reset", "--dir", d])
            self.assertFalse(os.path.isdir(os.path.join(d, ".sonarremedy")))

    def test_clean_and_reset_remove_sibling_worktrees(self):
        with tempfile.TemporaryDirectory() as parent:
            project = os.path.join(parent, "proj")
            os.makedirs(project)
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["init", "--dir", project])
            wt_root = os.path.join(parent, "proj-remedy-wtrees")
            os.makedirs(os.path.join(wt_root, "main"))
            self.assertTrue(os.path.isdir(wt_root))
            with contextlib.redirect_stdout(io.StringIO()):
                sonar_remedy.main(["clean", "--dir", project])
            self.assertFalse(os.path.isdir(wt_root))


class ScanSuppressionsCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(os.path.realpath(self.tmp.name), "config.json")
        rc.save(_valid_config(), self.config_path)

    def _run(self, argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return sonar_remedy.main(argv)

    def test_scan_suppressions_maps_repo(self):
        import sonar_suppressions

        with mock.patch.object(
            sonar_suppressions,
            "scan",
            return_value={"status": "scanned", "findings": [], "counts": {"total": 0}},
        ) as spatched:
            code = self._run(
                ["--config", self.config_path, "scan-suppressions", "--repo", self.tmp.name]
            )
        self.assertEqual(code, 0)
        self.assertEqual(spatched.call_args.args[0], self.tmp.name)


if __name__ == "__main__":
    unittest.main()
