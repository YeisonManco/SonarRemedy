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
            rc.normalize_sonar_url("https://sonar.example.com/"), "https://sonar.example.com"
        )

    def test_dashboard_url_reduces_to_base(self):
        self.assertEqual(
            rc.normalize_sonar_url("https://sonar.example.com/dashboard?id=my-project"),
            "https://sonar.example.com",
        )

    def test_proxy_path_stripped_of_query(self):
        self.assertEqual(
            rc.normalize_sonar_url("https://host/sonar/dashboard?x=1"),
            "https://host/sonar/dashboard",
        )

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
        self.assertEqual(rc.detect_project_key("https://h/dashboard?id=my-project"), "my-project")

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
            ["provider must be one of: " + ", ".join(rc.PROVIDERS)],
        )

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

    def test_load_missing_file_raises_config_error(self):
        with self.assertRaises(rc.ConfigError):
            rc.load(os.path.join(self.tmp.name, "missing.json"))

    def test_save_rejects_invalid(self):
        with self.assertRaises(rc.ConfigError):
            rc.save(_valid_config(provider="bogus"), self.path)

    def test_secret_never_in_file(self):
        cfg = _valid_config()
        rc.save(cfg, self.path)
        with open(self.path, encoding="utf-8") as handle:
            raw = handle.read()
        self.assertNotIn("sqa_", raw)
        self.assertEqual(json.loads(raw)["sonar"]["token_env"], "SONAR_TOKEN")


class PromptTests(unittest.TestCase):
    def test_prompt_uses_detected_key_and_envs_secret(self):
        saved_token = os.environ.get("SONAR_TOKEN")
        saved_pat = os.environ.get("GIT_PAT")
        self.addCleanup(lambda: self._restore("SONAR_TOKEN", saved_token))
        self.addCleanup(lambda: self._restore("GIT_PAT", saved_pat))

        inputs = iter(
            [
                "https://sonar.example.com/dashboard?id=my-project",  # sonar url
                "https://github.com/org/repo.git",  # repo url
                "C:/work/repo",  # local repo path
                "main",  # main branch
                "",  # propagation branches
                "C:/work/worktrees",  # worktree root
                "opencode",  # provider
            ]
        )

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


class NormalizeRepoUrlTests(unittest.TestCase):
    def test_strips_credentials_and_lowercases(self):
        self.assertEqual(
            rc.normalize_repo_url("https://User:TOKEN@dev.azure.com/Org/Repo.git"),
            "https://dev.azure.com/org/repo",
        )

    def test_strips_git_suffix(self):
        self.assertEqual(
            rc.normalize_repo_url("https://github.com/org/repo.git"),
            "https://github.com/org/repo",
        )

    def test_unquotes_path(self):
        self.assertEqual(
            rc.normalize_repo_url("https://dev.azure.com/Org/NUEVO%20REGFRO/_git/Repo"),
            "https://dev.azure.com/org/nuevo regfro/_git/repo",
        )

    def test_ssh_scp_like(self):
        self.assertEqual(
            rc.normalize_repo_url("git@github.com:org/repo.git"),
            "ssh://github.com/org/repo",
        )

    def test_empty(self):
        self.assertEqual(rc.normalize_repo_url(""), "")


class ResolveProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig = rc.projects_dir
        rc.projects_dir = lambda: os.path.join(self.tmp.name, "projects")
        self.addCleanup(lambda: setattr(rc, "projects_dir", self._orig))

    def _save(self, name, url, local_path=None):
        cfg = _valid_config()
        cfg["repository"]["url"] = url
        if local_path:
            cfg["repository"]["local_path"] = local_path
        else:
            cfg["repository"].pop("local_path", None)
        rc.save_project(name, cfg)

    def test_resolves_by_local_path(self):
        checkout = os.path.join(self.tmp.name, "checkout")
        os.makedirs(checkout)
        self._save("front", "https://dev.azure.com/org/proj", local_path=checkout)
        result = rc.resolve_project_for_checkout(checkout)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["project"], "front")

    def test_resolves_by_remote_url_with_embedded_credentials(self):
        checkout = os.path.join(self.tmp.name, "checkout")
        os.makedirs(checkout)
        self._save("front", "https://dev.azure.com/org/proj")
        result = rc.resolve_project_for_checkout(
            checkout, get_remote=lambda p: "https://pat:tok@dev.azure.com/org/proj"
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["project"], "front")

    def test_resolves_none(self):
        checkout = os.path.join(self.tmp.name, "checkout")
        os.makedirs(checkout)
        self._save("front", "https://dev.azure.com/org/proj")
        result = rc.resolve_project_for_checkout(
            checkout, get_remote=lambda p: "https://dev.azure.com/other/repo"
        )
        self.assertEqual(result["status"], "none")

    def test_resolves_ambiguous(self):
        checkout = os.path.join(self.tmp.name, "checkout")
        os.makedirs(checkout)
        self._save("front", "https://dev.azure.com/org/proj")
        self._save("front-alt", "https://dev.azure.com/org/proj")
        result = rc.resolve_project_for_checkout(
            checkout, get_remote=lambda p: "https://dev.azure.com/org/proj"
        )
        self.assertEqual(result["status"], "ambiguous")
        names = sorted(m["name"] for m in result["matches"])
        self.assertEqual(names, ["front", "front-alt"])


class ProjectCollisionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig = rc.projects_dir
        rc.projects_dir = lambda: os.path.join(self.tmp.name, "projects")
        self.addCleanup(lambda: setattr(rc, "projects_dir", self._orig))

    def _save(self, name, url, local_path=None):
        cfg = _valid_config()
        cfg["repository"]["url"] = url
        if local_path:
            cfg["repository"]["local_path"] = local_path
        else:
            cfg["repository"].pop("local_path", None)
        rc.save_project(name, cfg)

    def test_no_collisions(self):
        self._save("a", "https://dev.azure.com/org/a", local_path=os.path.join(self.tmp.name, "a"))
        self._save("b", "https://dev.azure.com/org/b", local_path=os.path.join(self.tmp.name, "b"))
        self.assertEqual(rc.list_project_collisions(), [])

    def test_collision_same_local_path(self):
        shared = os.path.join(self.tmp.name, "shared")
        self._save("a", "https://dev.azure.com/org/a", local_path=shared)
        self._save("b", "https://dev.azure.com/org/b", local_path=shared)
        collisions = rc.list_project_collisions()
        self.assertEqual(len(collisions), 1)
        self.assertEqual({collisions[0]["project_a"], collisions[0]["project_b"]}, {"a", "b"})
        self.assertEqual(collisions[0]["reason"], "local_path")

    def test_collision_same_remote_url(self):
        self._save("a", "https://dev.azure.com/org/repo")
        self._save("b", "https://dev.azure.com/org/repo.git")
        collisions = rc.list_project_collisions()
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0]["reason"], "remote_url")


class UrlCredentialsTests(unittest.TestCase):
    def test_detects_userinfo(self):
        self.assertTrue(rc.url_embeds_credentials("https://pat@dev.azure.com/org/repo"))

    def test_detects_user_and_password(self):
        self.assertTrue(rc.url_embeds_credentials("https://user:pass@host/repo"))

    def test_no_credentials(self):
        self.assertFalse(rc.url_embeds_credentials("https://dev.azure.com/org/repo"))

    def test_empty(self):
        self.assertFalse(rc.url_embeds_credentials(""))


class QueueRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig = rc.queue_registry_path
        rc.queue_registry_path = lambda: os.path.join(self.tmp.name, "queues.json")
        self.addCleanup(lambda: setattr(rc, "queue_registry_path", self._orig))

    def test_register_and_lookup(self):
        state = os.path.join(self.tmp.name, "q1")
        rc.register_queue("front", state)
        self.assertEqual(rc.project_for_queue(state), "front")
        self.assertEqual(rc.queues_for_project("front"), [rc.canonical_path(state)])

    def test_project_for_queue_unknown(self):
        self.assertIsNone(rc.project_for_queue(os.path.join(self.tmp.name, "nope")))

    def test_register_idempotent(self):
        state = os.path.join(self.tmp.name, "q1")
        rc.register_queue("front", state)
        rc.register_queue("front", state)
        self.assertEqual(len(rc.queues_for_project("front")), 1)

    def test_corrupt_registry_ignored(self):
        with open(rc.queue_registry_path(), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(rc.project_for_queue(os.path.join(self.tmp.name, "q")), None)
        self.assertEqual(rc.queues_for_project("front"), [])


class DetectBranchTests(unittest.TestCase):
    def test_from_branch_query(self):
        self.assertEqual(
            rc.detect_branch("https://h/dashboard?id=p&branch=feature/Sonar"), "feature/Sonar"
        )

    def test_no_branch(self):
        self.assertEqual(rc.detect_branch("https://h/dashboard?id=p"), "")


if __name__ == "__main__":
    unittest.main()
