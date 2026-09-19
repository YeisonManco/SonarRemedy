"""Offline fixtures only: no live Sonar endpoint, git checkout or credentials required."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import debtpack as d
import sonar_fetch as f

PACK = Path(__file__).resolve().parents[1]


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=PACK / ".debt-state")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "src").mkdir()
        for name in ("A.cs", "B.cs", "C.cs", "D.cs"):
            (self.repo / "src" / name).write_text("fixture", encoding="utf-8")
        self.raw = {
            "adapter": "api",
            "repo": str(self.repo),
            "url": "https://sonar.example.test",
            "trusted_url": "https://sonar.example.test",
            "project": "fixture",
            "branch": "feature/test",
            "timeout": 30,
        }
        self.config = f.Config(self.raw)
        self.calls = []
        self.responses = {
            "api/project_analyses/search": {
                "analyses": [
                    {
                        "key": "analysis1",
                        "date": datetime.now(UTC).isoformat(),
                        "revision": "abc123",
                    }
                ]
            },
            "api/qualitygates/project_status": {"projectStatus": {"status": "OK"}},
            "api/measures/component": {
                "component": {
                    "key": "fixture",
                    "measures": [
                        {"metric": k, "value": v}
                        for k, v in {
                            "coverage": "91",
                            "duplicated_lines_density": "4",
                            "code_smells": "0",
                            "vulnerabilities": "0",
                            "security_hotspots": "1",
                            "security_hotspots_reviewed": "100",
                        }.items()
                    ],
                }
            },
            "api/issues/search": {
                "paging": {"pageIndex": 1, "pageSize": 100, "total": 3},
                "issues": [
                    {
                        "key": "S1",
                        "project": "fixture",
                        "component": "fixture:src/A.cs",
                        "line": 2,
                        "type": "CODE_SMELL",
                        "rule": "csharpsquid:S2094",
                    },
                    {
                        "key": "V1",
                        "project": "fixture",
                        "component": "fixture:src/B.cs",
                        "line": 3,
                        "type": "VULNERABILITY",
                        "rule": "csharpsquid:S2077",
                    },
                    {
                        "key": "B1",
                        "project": "fixture",
                        "component": "fixture:src/C.cs",
                        "line": 4,
                        "type": "BUG",
                        "rule": "csharpsquid:S3776",
                    },
                ],
            },
            "api/hotspots/search": {
                "paging": {"pageIndex": 1, "pageSize": 100, "total": 1},
                "hotspots": [
                    {
                        "key": "H1",
                        "project": "fixture",
                        "component": "fixture:src/D.cs",
                        "line": 5,
                        "status": "TO_REVIEW",
                        "rule": "csharpsquid:S2094",
                    }
                ],
            },
            "api/rules/search": {
                "total": 3,
                "p": 1,
                "ps": 50,
                "rules": [
                    {
                        "key": "csharpsquid:S2077",
                        "name": "Formatting SQL queries is security-sensitive",
                        "description": "D" * 250,
                        "remediation": {"func": "CONSTANT_ISSUE", "constantCost": "30min"},
                    },
                    {
                        "key": "csharpsquid:S2094",
                        "name": "Classes should not be empty",
                        "description": "E" * 250,
                    },
                    {
                        "key": "csharpsquid:S3776",
                        "name": "Cognitive Complexity of functions should not be too high",
                        "description": "F" * 250,
                    },
                ],
            },
        }

    def get(self, path, **params):
        self.calls.append((path, params))
        return copy.deepcopy(self.responses[path])

    def fetch(self, get=None, head="abc123"):
        get = get or self.get

        class FakeClient:
            def __init__(inner, url, secret, timeout=30, allow_http=False):
                inner.url, inner.secret, inner.timeout = url, secret, timeout
                inner.allow_http = allow_http
                self.client_url, self.client_secret = inner.url, inner.secret

            def get(inner, path, **params):
                return get(path, **params)

        with (
            patch.dict(os.environ, {"SONAR_TOKEN": "fixture-private-value"}),
            patch.object(f, "Client", FakeClient),
            patch.object(f, "git_head", return_value=head),
        ):
            return f.fetch(self.config, self.root / "runs")

    def test_configuration_fails_closed(self):
        for changes in (
            {"url": "http://sonar.example.test"},
            {"trusted_url": "https://other.test"},
            {"url": "https://user:pass@sonar.example.test"},
            {"url": "https://sonar.example.test/?token=x"},
            {"adapter": "node"},
            {"timeout": 0},
            {"timeout": "30"},
            {"skip_tests": True},
            {"token": "bad"},
            {"repo": str(self.root / "missing")},
        ):
            with self.subTest(changes=changes), self.assertRaises(f.Blocked):
                f.Config(dict(self.raw, **changes))
        with self.assertRaises(f.Blocked):
            f.Config({})

    def test_missing_token_blocks(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(f.Blocked):
            f.fetch(self.config, self.root / "runs")

    def test_output_inside_repo_blocks(self):
        with (
            patch.dict(os.environ, {"SONAR_TOKEN": "fixture-private-value"}),
            self.assertRaises(f.Blocked),
        ):
            f.fetch(self.config, self.repo / "runs")

    def test_fetch_complete_normalizes_and_export_passes_plan(self):
        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch()
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["revision"], "abc123")
        self.assertEqual(result["gate"], "OK")
        self.assertEqual(
            result["measures"],
            {
                "coverage": 91.0,
                "duplication": 4.0,
                "smells": 0.0,
                "vulnerabilities": 0.0,
                "hotspots": 1.0,
            },
        )
        self.assertEqual(result["issues_total"], 4)
        self.assertEqual(result["issues_by_kind"], {"smells": 2, "security": 2})
        self.assertEqual(result["unreviewed_hotspots"], 1)
        self.assertEqual(result["warnings"], [])
        self.assertNotIn("issues", result)
        self.assertNotIn("analysis_warning", result)
        self.assertNotIn("hotspots_warning", result)
        self.assertEqual(self.client_url, "https://sonar.example.test")
        self.assertEqual(self.client_secret, "fixture-private-value")
        export = json.loads((self.root / "runs" / "export.json").read_text(encoding="utf-8"))
        self.assertEqual(
            export,
            {
                "version": 1,
                "revision": "abc123",
                "issues": [
                    {
                        "id": "S1",
                        "kind": "smells",
                        "path": "src/A.cs",
                        "line": 2,
                        "rule": "csharpsquid:S2094",
                    },
                    {
                        "id": "V1",
                        "kind": "security",
                        "path": "src/B.cs",
                        "line": 3,
                        "rule": "csharpsquid:S2077",
                    },
                    {
                        "id": "B1",
                        "kind": "smells",
                        "path": "src/C.cs",
                        "line": 4,
                        "rule": "csharpsquid:S3776",
                    },
                    {
                        "id": "H1",
                        "kind": "security",
                        "path": "src/D.cs",
                        "line": 5,
                        "rule": "csharpsquid:S2094",
                    },
                ],
            },
        )
        planned = d.plan(self.repo, export, limit=4)
        self.assertEqual(planned["revision"], "abc123")
        self.assertEqual(len(planned["tasks"]), 4)
        self.assertEqual(planned["remaining"], 0)

    def test_reviewed_hotspots_and_unsupported_dispositions(self):
        self.responses["api/hotspots/search"]["hotspots"] = [
            {
                "key": "H1",
                "project": "fixture",
                "component": "fixture:src/D.cs",
                "line": 5,
                "status": "REVIEWED",
                "resolution": "SAFE",
            }
        ]
        result = self.fetch()
        self.assertEqual(result["unreviewed_hotspots"], 0)
        self.assertEqual(result["issues_total"], 3)
        for disposition in (
            {"status": "OPEN"},
            {"status": "REVIEWED"},
            {"status": "REVIEWED", "resolution": "OTHER"},
        ):
            with self.subTest(disposition=disposition):
                self.responses["api/hotspots/search"]["hotspots"] = [
                    {
                        "key": "H1",
                        "project": "fixture",
                        "component": "fixture:src/D.cs",
                        "line": 5,
                        **disposition,
                    }
                ]
                with self.assertRaises(f.Blocked):
                    self.fetch()

    def test_revision_mismatch_blocks(self):
        with self.assertRaises(f.Blocked):
            self.fetch(head="otherrev")

    def test_analysis_lookup_403_degrades_to_head(self):
        def get(path, **params):
            if path == "api/project_analyses/search":
                raise f.Blocked("project analyses forbidden")
            return copy.deepcopy(self.responses[path])

        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch(get=get)
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["revision"], "abc123")
        self.assertIn("analysis_warning", result)
        self.assertTrue(result["analysis_warning"].startswith("analysis lookup blocked"))
        export = json.loads((self.root / "runs" / "export.json").read_text(encoding="utf-8"))
        self.assertEqual(export["revision"], "abc123")
        self.assertEqual(len(export["issues"]), 4)

    def test_analysis_lookup_http_403_degrades_to_head(self):
        # A real server 403 surfaces as urllib HTTPError, not Blocked.
        from urllib.error import HTTPError

        def get(path, **params):
            if path == "api/project_analyses/search":
                raise HTTPError(
                    "https://sonar.example.test/api/project_analyses/search",
                    403,
                    "Forbidden",
                    [],
                    None,
                )
            return copy.deepcopy(self.responses[path])

        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch(get=get)
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["revision"], "abc123")
        self.assertTrue(result["analysis_warning"].startswith("analysis lookup blocked"))
        export = json.loads((self.root / "runs" / "export.json").read_text(encoding="utf-8"))
        self.assertEqual(export["revision"], "abc123")

    def test_analysis_lookup_empty_degrades_to_head(self):
        self.responses["api/project_analyses/search"] = {"analyses": []}
        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch()
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["revision"], "abc123")
        self.assertEqual(
            result["analysis_warning"], "no branch analysis found; using local HEAD as revision"
        )
        export = json.loads((self.root / "runs" / "export.json").read_text(encoding="utf-8"))
        self.assertEqual(export["revision"], "abc123")

    def test_analysis_revision_invalid_degrades_to_head(self):
        self.responses["api/project_analyses/search"] = {
            "analyses": [{"key": "analysis1", "revision": "bad rev!"}]
        }
        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch()
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["revision"], "abc123")
        self.assertEqual(
            result["analysis_warning"], "analysis revision missing or invalid; using local HEAD"
        )
        export = json.loads((self.root / "runs" / "export.json").read_text(encoding="utf-8"))
        self.assertEqual(export["revision"], "abc123")

    def test_hotspots_lookup_403_degrades_to_issues_only(self):
        def get(path, **params):
            if path == "api/hotspots/search":
                raise f.Blocked("hotspots forbidden")
            return copy.deepcopy(self.responses[path])

        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch(get=get)
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["issues_total"], 3)
        self.assertIn("hotspots_warning", result)
        self.assertTrue(result["hotspots_warning"].startswith("hotspot lookup blocked"))
        self.assertIsNone(result["unreviewed_hotspots"])
        self.assertEqual(result["issues_by_kind"], {"smells": 2, "security": 1})
        export = json.loads((self.root / "runs" / "export.json").read_text(encoding="utf-8"))
        self.assertEqual(len(export["issues"]), 3)
        self.assertTrue(all(issue["id"] != "H1" for issue in export["issues"]))

    def test_hotspots_lookup_http_403_degrades_to_issues_only(self):
        # A real server 403 surfaces as urllib HTTPError, not Blocked.
        from urllib.error import HTTPError

        def get(path, **params):
            if path == "api/hotspots/search":
                raise HTTPError(
                    "https://sonar.example.test/api/hotspots/search", 403, "Forbidden", [], None
                )
            return copy.deepcopy(self.responses[path])

        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch(get=get)
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["issues_total"], 3)
        self.assertTrue(result["hotspots_warning"].startswith("hotspot lookup blocked"))
        self.assertIsNone(result["unreviewed_hotspots"])
        export = json.loads((self.root / "runs" / "export.json").read_text(encoding="utf-8"))
        self.assertEqual(len(export["issues"]), 3)

    def test_hotspots_unsupported_disposition_still_blocks_when_endpoint_responds(self):
        # Degradation only applies when the lookup itself fails; a responding
        # endpoint with an unsupported disposition stays strict.
        self.responses["api/hotspots/search"]["hotspots"] = [
            {
                "key": "H1",
                "project": "fixture",
                "component": "fixture:src/D.cs",
                "line": 5,
                "status": "REVIEWED",
                "resolution": "OTHER",
            }
        ]
        with self.assertRaises(f.Blocked):
            self.fetch()

    def test_chunk_issues(self):
        self.assertEqual(f.chunk_issues([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])
        self.assertEqual(f.chunk_issues([1, 2], 2), [[1, 2]])
        self.assertEqual(f.chunk_issues([], 2), [])

    def test_large_export_is_chunked(self):
        with patch.object(f, "MAX_ISSUES", 2):
            result = self.fetch()
        self.assertEqual(result["chunks"], 2)
        self.assertEqual(len(result["exports"]), 2)

    def test_fetch_reports_project_and_branch(self):
        result = self.fetch()
        self.assertEqual(result["project"], "fixture")
        self.assertEqual(result["branch"], self.config.branch)

    def test_fetch_skips_issues_without_line(self):
        del self.responses["api/issues/search"]["issues"][0]["line"]
        result = self.fetch()
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["deferred_no_line"], 1)
        self.assertEqual(result["issues_total"], 3)

    def test_fetch_with_zero_issues_writes_empty_export(self):
        def get(path, **params):
            if path == "api/issues/search":
                return copy.deepcopy(
                    {"paging": {"pageIndex": 1, "pageSize": 100, "total": 0}, "issues": []}
                )
            if path == "api/hotspots/search":
                return copy.deepcopy(
                    {"paging": {"pageIndex": 1, "pageSize": 100, "total": 0}, "hotspots": []}
                )
            return copy.deepcopy(self.responses[path])

        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch(get=get)
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["issues_total"], 0)
        self.assertEqual(len(result["exports"]), 1)

    def test_pagination_inconsistency_blocks(self):
        items = [
            {
                "key": f"S{i:03d}",
                "project": "fixture",
                "component": "fixture:src/A.cs",
                "line": 1,
                "type": "CODE_SMELL",
            }
            for i in range(100)
        ]

        def get(path, **params):
            if path == "api/issues/search":
                return copy.deepcopy(
                    {
                        "paging": {
                            "pageIndex": params["p"],
                            "pageSize": 100,
                            "total": 150 if params["p"] == 1 else 149,
                        },
                        "issues": items if params["p"] == 1 else [],
                    }
                )
            return copy.deepcopy(self.responses[path])

        with self.assertRaises(f.Blocked):
            self.fetch(get=get)

    def test_missing_metric_never_means_zero(self):
        self.responses["api/measures/component"]["component"]["measures"].pop(2)
        with self.assertRaises(f.Blocked):
            self.fetch()

    def test_foreign_project_component_blocks(self):
        self.responses["api/issues/search"]["issues"][0] = {
            "key": "S1",
            "project": "other",
            "component": "other:a.cs",
            "line": 1,
            "type": "CODE_SMELL",
            "rule": "csharpsquid:S2094",
        }
        with self.assertRaises(f.Blocked):
            self.fetch()

    def test_dirty_tree_warns_but_does_not_block(self):
        with patch.object(f, "git_dirty", return_value=True):
            result = self.fetch()
        self.assertEqual(
            result["warnings"],
            [
                "working tree is not clean; export binds the analyzed revision, not uncommitted edits"
            ],
        )

    def test_rules_json_written_compact(self):
        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch()
        rules = json.loads((self.root / "runs" / "rules.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(rules), {"csharpsquid:S2094", "csharpsquid:S2077", "csharpsquid:S3776"}
        )
        self.assertEqual(
            rules["csharpsquid:S2077"]["name"], "Formatting SQL queries is security-sensitive"
        )
        self.assertLessEqual(len(rules["csharpsquid:S2094"]["description"]), 200)
        self.assertEqual(rules["csharpsquid:S2077"]["remediation"]["func"], "CONSTANT_ISSUE")
        self.assertEqual(result["rules_count"], 3)
        self.assertTrue(result["rules"].endswith("rules.json"))
        self.assertNotIn("rules_warning", result)
        calls = [c for c in self.calls if c[0] == "api/rules/search"]
        self.assertEqual(
            calls[0][1]["rule_keys"], "csharpsquid:S2077,csharpsquid:S2094,csharpsquid:S3776"
        )

    def test_rules_lookup_failure_does_not_block_fetch(self):
        def get(path, **params):
            if path == "api/rules/search":
                raise f.Blocked("rules denied")
            return copy.deepcopy(self.responses[path])

        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch(get=get)
        self.assertEqual(result["status"], "collected")
        self.assertIn("rules_warning", result)
        self.assertTrue(result["rules_warning"].startswith("rules lookup failed"))
        self.assertNotIn("rules", result)
        self.assertFalse((self.root / "runs" / "rules.json").exists())
        export = json.loads((self.root / "runs" / "export.json").read_text(encoding="utf-8"))
        self.assertEqual(len(export["issues"]), 4)

    def test_git_head_requires_root_and_valid_revision(self):
        def git(argv, **kwargs):
            command = argv[3]
            output = (
                str(self.repo).encode()
                if argv[3:] == ["rev-parse", "--show-toplevel"]
                else b"abc123"
                if command == "rev-parse"
                else b""
            )
            return subprocess.CompletedProcess(argv, 0, output, b"")

        with patch.object(f.subprocess, "run", side_effect=git):
            self.assertEqual(f.git_head(self.repo), "abc123")

        def fail(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, b"", b"")

        with patch.object(f.subprocess, "run", side_effect=fail), self.assertRaises(f.Blocked):
            f.git_head(self.repo)
        other = self.root / "other"
        other.mkdir()
        with patch.object(f.subprocess, "run", side_effect=git), self.assertRaises(f.Blocked):
            f.git_head(other)

    def test_severities_field_accepts_known_values_and_rejects_others(self):
        for value in ("BLOCKER", "BLOCKER,CRITICAL", "MINOR,INFO,MAJOR"):
            with self.subTest(value=value):
                f.Config(dict(self.raw, severities=value))
        for value in (
            "blocker",
            "BLOCKER,",
            ",CRITICAL",
            "BLOCKER;CRITICAL",
            "BLOCKER,BOGUS",
            "BLOCKER,BLOCKER",
            "",
            123,
            ["BLOCKER"],
        ):
            with self.subTest(value=value), self.assertRaises(f.Blocked):
                f.Config(dict(self.raw, severities=value))

    def test_severities_omitted_by_default(self):
        config = f.Config(self.raw)
        self.assertIsNone(config.severities)

    def test_exclude_ids_field_validates(self):
        f.Config(dict(self.raw, exclude_ids=["H1"]))
        f.Config(dict(self.raw, exclude_ids=["H1", "S1"]))
        for value in ([], ["H1", "H1"], ["bad key!"], "H1", [1], None if False else [""]):
            with self.subTest(value=value), self.assertRaises(f.Blocked):
                f.Config(dict(self.raw, exclude_ids=value))

    def test_exclude_ids_omitted_by_default(self):
        config = f.Config(self.raw)
        self.assertIsNone(config.exclude_ids)

    def test_exclude_ids_removes_matching_issue_and_warns(self):
        raw = dict(self.raw, exclude_ids=["S1"])
        self.config = f.Config(raw)
        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch()
        self.assertEqual(result["issues_total"], 3)
        self.assertIn("excluded 1 explicitly configured issue(s)", result["warnings"])
        export = json.loads((self.root / "runs" / "export.json").read_text(encoding="utf-8"))
        self.assertTrue(all(issue["id"] != "S1" for issue in export["issues"]))

    def test_exclude_ids_with_no_match_reports_no_warning(self):
        raw = dict(self.raw, exclude_ids=["does-not-exist"])
        self.config = f.Config(raw)
        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch()
        self.assertEqual(result["issues_total"], 4)
        self.assertEqual(result["warnings"], [])

    def test_severities_filter_narrows_issue_search_and_still_normalizes(self):
        self.responses["api/issues/search"]["issues"] = [
            {
                "key": "S1",
                "project": "fixture",
                "component": "fixture:src/A.cs",
                "line": 2,
                "type": "CODE_SMELL",
                "rule": "csharpsquid:S2094",
            }
        ]
        self.responses["api/issues/search"]["paging"]["total"] = 1
        raw = dict(self.raw, severities="BLOCKER,CRITICAL")
        self.config = f.Config(raw)
        with patch.object(f, "git_dirty", return_value=False):
            result = self.fetch()
        self.assertEqual(result["issues_total"], 2)
        issue_calls = [params for path, params in self.calls if path == "api/issues/search"]
        self.assertTrue(issue_calls)
        for params in issue_calls:
            self.assertEqual(params.get("severities"), "BLOCKER,CRITICAL")
        hotspot_calls = [params for path, params in self.calls if path == "api/hotspots/search"]
        for params in hotspot_calls:
            self.assertNotIn("severities", params)

    def test_main_exit_codes(self):
        import contextlib
        import io

        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch.object(sys, "argv", ["sonar_fetch.py", "config.json"]),
            patch.object(f, "read_json", return_value={}),
            patch.object(f, "Config", return_value=object()),
            patch.object(f, "fetch", return_value={"status": "collected"}),
        ):
            self.assertEqual(f.main(), 0)
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch.object(sys, "argv", ["sonar_fetch.py", "config.json"]),
            patch.object(f, "read_json", return_value={}),
            patch.object(f, "Config", return_value=object()),
            patch.object(f, "fetch", side_effect=f.Blocked("fixture-private blocked")),
        ):
            self.assertEqual(f.main(), 2)


if __name__ == "__main__":
    unittest.main()
