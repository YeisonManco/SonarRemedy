"""Explicit HTTP opt-in for the Sonar fetch (on-prem HTTP servers)."""

import os
import tempfile
import unittest

import sonar_client
import sonar_fetch
import sonar_remedy
import sonar_remedy_config as rc


class EndpointHttpTests(unittest.TestCase):
    def test_http_rejected_by_default(self):
        with self.assertRaises(sonar_client.Blocked):
            sonar_client.endpoint("http://sonar.local:9000")

    def test_http_allowed_when_opted_in(self):
        self.assertEqual(
            sonar_client.endpoint("http://sonar.local:9000", allow_http=True),
            "http://sonar.local:9000",
        )

    def test_https_still_allowed(self):
        self.assertEqual(
            sonar_client.endpoint("https://sonar.example.com", allow_http=True),
            "https://sonar.example.com",
        )


class FetchConfigHttpTests(unittest.TestCase):
    def test_config_accepts_http_with_allow_http(self):
        with tempfile.TemporaryDirectory() as repo:
            cfg = sonar_fetch.Config(
                {
                    "adapter": "api",
                    "repo": repo,
                    "url": "http://sonar.local:9000",
                    "trusted_url": "http://sonar.local:9000",
                    "project": "p",
                    "branch": "main",
                    "allow_http": True,
                }
            )
            self.assertEqual(cfg.url, "http://sonar.local:9000")
            self.assertTrue(cfg.allow_http)

    def test_config_rejects_http_without_allow_http(self):
        with tempfile.TemporaryDirectory() as repo, self.assertRaises(sonar_client.Blocked):
            sonar_fetch.Config(
                {
                    "adapter": "api",
                    "repo": repo,
                    "url": "http://sonar.local:9000",
                    "trusted_url": "http://sonar.local:9000",
                    "project": "p",
                    "branch": "main",
                }
            )


def _valid_rcfg(**overrides):
    cfg = {
        "version": 1,
        "sonar": {
            "url": "https://sonar.example.com",
            "project_key": "p",
            "token_env": "SONAR_TOKEN",
        },
        "repository": {
            "url": "https://github.com/o/r.git",
            "pat_env": "GIT_PAT",
            "main_branch": "main",
            "propagation_branches": [],
        },
        "worktrees": {"root": "C:/w"},
        "provider": "manual",
    }
    cfg.update(overrides)
    return cfg


class RecoverConfigHttpTests(unittest.TestCase):
    def test_allow_http_optional_and_bool(self):
        self.assertEqual(rc.validate(_valid_rcfg()), [])
        cfg = _valid_rcfg()
        cfg["sonar"]["allow_http"] = True
        self.assertEqual(rc.validate(cfg), [])

    def test_allow_http_must_be_bool(self):
        cfg = _valid_rcfg()
        cfg["sonar"]["allow_http"] = "yes"
        self.assertTrue(any("allow_http" in e for e in rc.validate(cfg)))

    def test_prompt_sets_allow_http_for_http_url(self):
        saved_token = os.environ.get("SONAR_TOKEN")
        saved_pat = os.environ.get("GIT_PAT")
        self.addCleanup(lambda: self._restore("SONAR_TOKEN", saved_token))
        self.addCleanup(lambda: self._restore("GIT_PAT", saved_pat))
        inputs = iter(
            [
                "http://sonar.local:9000",  # sonar url
                "myproj",  # project key (not detected)
                "yes",  # allow_http (http url)
                "https://github.com/o/r.git",  # repo url
                "C:/repo",  # local path
                "main",  # main branch
                "",  # propagation
                "C:/wt",  # worktree root
                "manual",  # provider
            ]
        )
        cfg = rc.prompt(
            input_fn=lambda *a, **k: next(inputs),
            secret_fn=lambda *a, **k: (
                "sqa_TEST" if a and a[0].startswith("SonarQube token") else "ghp_TEST"
            ),
        )
        self.assertTrue(cfg["sonar"]["allow_http"])

    @staticmethod
    def _restore(name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class BuildFetchConfigHttpTests(unittest.TestCase):
    def test_build_fetch_config_maps_allow_http(self):
        with tempfile.TemporaryDirectory() as repo:
            rcfg = _valid_rcfg()
            rcfg["sonar"]["url"] = "http://sonar.local:9000"
            rcfg["sonar"]["allow_http"] = True
            cfg = sonar_remedy.build_fetch_config(rcfg, repo)
            self.assertEqual(cfg.url, "http://sonar.local:9000")
            self.assertTrue(cfg.allow_http)


if __name__ == "__main__":
    unittest.main()
