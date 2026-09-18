"""Tests for the SonarRemedy facade that drives the pipeline from config."""

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import sonar_fetch
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
        self.assertEqual(qpatched.call_args.kwargs.get("branch"), "main")
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
        self.assertIn("-ProjectBaseDir", argv)
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


class InitCommandTests(unittest.TestCase):
    def test_init_writes_mcp_and_instructions(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                code = sonar_remedy.main(["init", "--dir", d])
            self.assertEqual(code, 0)
            mcp_path = os.path.join(d, ".vscode", "mcp.json")
            instr_path = os.path.join(d, ".github", "copilot-instructions.md")
            self.assertTrue(os.path.isfile(mcp_path))
            self.assertTrue(os.path.isfile(instr_path))
            with open(mcp_path, encoding="utf-8") as fh:
                mcp_content = fh.read()
            self.assertIn("sonar-remedy", mcp_content)
            self.assertIn("sonar_remedy_mcp.py", mcp_content)
            with open(instr_path, encoding="utf-8") as fh:
                instr_content = fh.read()
            self.assertIn("sonar_remedy_", instr_content)


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
