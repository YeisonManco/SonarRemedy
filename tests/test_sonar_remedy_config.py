"""Tests for the SonarRemedy first-run config wizard and store."""
import json
import os
import tempfile
import unittest

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


class NormalizeTests(unittest.TestCase):
    def test_base_url_unchanged(self):
        self.assertEqual(
            rc.normalize_sonar_url("https://sonar.example.com/"),
            "https://sonar.example.com")

    def test_dashboard_url_reduces_to_base(self):
        self.assertEqual(
            rc.normalize_sonar_url("https://sonar.example.com/dashboard?id=my-project"),
            "https://sonar.example.com")

    def test_proxy_path_stripped_of_query(self):
        self.assertEqual(
            rc.normalize_sonar_url("https://host/sonar/dashboard?x=1"),
            "https://host/sonar/dashboard")

    def test_rejects_embedded_sqa_token(self):
        with self.assertRaises(rc.ConfigError):
            rc.normalize_sonar_url("https://host/dashboard?sqa_abc123")

    def test_rejects_query_token_param(self):
        with self.assertRaises(rc.ConfigError):
            rc.normalize_sonar_url("https://host/dashboard?token=abc")

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(rc.ConfigError):
            rc.normalize_sonar_url("file:///etc/passwd")

    def test_rejects_empty(self):
        with self.assertRaises(rc.ConfigError):
            rc.normalize_sonar_url("")


class DetectTests(unittest.TestCase):
    def test_from_id_query(self):
        self.assertEqual(
            rc.detect_project_key("https://h/dashboard?id=my-project"), "my-project")

    def test_from_last_path_segment(self):
        self.assertEqual(rc.detect_project_key("https://h/my-project"), "my-project")

    def test_empty(self):
        self.assertEqual(rc.detect_project_key("https://h/"), "")


class ValidateTests(unittest.TestCase):
    def test_valid_config(self):
        self.assertEqual(rc.validate(_valid_config()), [])

    def test_missing_sonar_url(self):
        cfg = _valid_config()
        cfg["sonar"]["url"] = ""
        self.assertTrue(any("sonar.url" in e for e in rc.validate(cfg)))

    def test_bad_provider(self):
        self.assertEqual(
            rc.validate(_valid_config(provider="nope")),
            ["provider must be one of: " + ", ".join(rc.PROVIDERS)])

    def test_token_env_not_env_name(self):
        cfg = _valid_config()
        cfg["sonar"]["token_env"] = "not an env name!"
        self.assertTrue(any("token_env" in e for e in rc.validate(cfg)))

    def test_missing_pat_env(self):
        cfg = _valid_config()
        cfg["repository"]["pat_env"] = ""
        self.assertTrue(any("pat_env" in e for e in rc.validate(cfg)))

    def test_empty_project_key(self):
        cfg = _valid_config()
        cfg["sonar"]["project_key"] = ""
        self.assertTrue(any("project_key" in e for e in rc.validate(cfg)))

    def test_local_path_optional(self):
        cfg = _valid_config()
        del cfg["repository"]["local_path"]
        self.assertEqual(rc.validate(cfg), [])


class SaveLoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "config.json")

    def test_round_trip(self):
        cfg = _valid_config()
        rc.save(cfg, self.path)
        self.assertEqual(rc.load(self.path), cfg)

    def test_save_rejects_invalid(self):
        with self.assertRaises(rc.ConfigError):
            rc.save(_valid_config(provider="bogus"), self.path)

    def test_secret_never_in_file(self):
        cfg = _valid_config()
        rc.save(cfg, self.path)
        with open(self.path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        self.assertNotIn("sqa_", raw)
        self.assertEqual(json.loads(raw)["sonar"]["token_env"], "SONAR_TOKEN")


class PromptTests(unittest.TestCase):
    def test_prompt_uses_detected_key_and_envs_secret(self):
        saved_token = os.environ.get("SONAR_TOKEN")
        saved_pat = os.environ.get("GIT_PAT")
        self.addCleanup(lambda: self._restore("SONAR_TOKEN", saved_token))
        self.addCleanup(lambda: self._restore("GIT_PAT", saved_pat))

        inputs = iter([
            "https://sonar.example.com/dashboard?id=my-project",  # sonar url
            "https://github.com/org/repo.git",                    # repo url
            "C:/work/repo",                                       # local repo path
            "main",                                               # main branch
            "",                                                   # propagation branches
            "C:/work/worktrees",                                  # worktree root
            "opencode",                                           # provider
        ])

        def fake_secret(*args, **kwargs):
            message = args[0] if args else ""
            if message.startswith("SonarQube token"):
                return "sqa_SECRETTOKEN"
            return "ghp_SECRETPAT"

        cfg = rc.prompt(input_fn=lambda *a, **k: next(inputs), secret_fn=fake_secret)

        self.assertEqual(cfg["sonar"]["project_key"], "my-project")
        self.assertEqual(cfg["sonar"]["token_env"], "SONAR_TOKEN")
        self.assertEqual(cfg["repository"]["pat_env"], "GIT_PAT")
        self.assertEqual(cfg["repository"]["local_path"], "C:/work/repo")
        self.assertEqual(os.environ["SONAR_TOKEN"], "sqa_SECRETTOKEN")
        self.assertEqual(os.environ["GIT_PAT"], "ghp_SECRETPAT")
        serialized = json.dumps(cfg)
        self.assertNotIn("SECRETTOKEN", serialized)
        self.assertNotIn("SECRETPAT", serialized)

    @staticmethod
    def _restore(name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig = rc.projects_dir
        rc.projects_dir = lambda: os.path.join(self.tmp.name, "projects")
        self.addCleanup(lambda: setattr(rc, "projects_dir", self._orig))

    def test_save_and_load_roundtrip(self):
        rc.save_project("documentos", _valid_config())
        self.assertEqual(rc.load_project("documentos"), _valid_config())

    def test_list_projects_sorted(self):
        rc.save_project("documentos", _valid_config())
        rc.save_project("firmadigital", _valid_config())
        self.assertEqual(rc.list_projects(), ["documentos", "firmadigital"])

    def test_load_missing_project(self):
        with self.assertRaises(rc.ConfigError):
            rc.load_project("no-existe")

    def test_project_name_rejected(self):
        with self.assertRaises(rc.ConfigError):
            rc.project_path("../escape")


class DetectBranchTests(unittest.TestCase):
    def test_from_branch_query(self):
        self.assertEqual(rc.detect_branch("https://h/dashboard?id=p&branch=feature/Sonar"),
                         "feature/Sonar")

    def test_no_branch(self):
        self.assertEqual(rc.detect_branch("https://h/dashboard?id=p"), "")


if __name__ == "__main__":
    unittest.main()
