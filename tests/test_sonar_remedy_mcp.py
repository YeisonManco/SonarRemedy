"""Tests for the SonarRemedy MCP server tool registry."""

import unittest
from unittest import mock

import sonar_remedy
import sonar_remedy_mcp as mcp

# The sonar_remedy.py subcommands. Keep in sync when you add a command: adding a
# command here AND a tool in sonar_remedy_mcp.py is one change (see CONTRIBUTING.md).
COMMANDS = [
    "fetch",
    "slice",
    "run",
    "configure",
    "integrate",
    "projects",
    "status",
    "progress",
    "schedule",
    "analyze",
    "run-all",
    "autopilot",
    "detect-checks",
    "configure-project",
    "scan-suppressions",
    "scan-exclusions",
    "rules",
    "report",
    "doctor",
]


class ToolRegistryTests(unittest.TestCase):
    def test_tools_cover_all_commands(self):
        tool_names = {t["name"] for t in mcp.TOOLS}
        for cmd in COMMANDS:
            self.assertIn(
                "sonar_remedy_" + cmd.replace("-", "_"),
                tool_names,
                f"missing MCP tool for command: {cmd}",
            )

    def test_build_argv_fetch(self):
        argv = mcp.build_argv("sonar_remedy_fetch", {"project": "doc", "repo": "C:/r"})
        self.assertEqual(argv, ["--project", "doc", "fetch", "--repo", "C:/r"])

    def test_build_argv_fetch_with_kinds(self):
        argv = mcp.build_argv(
            "sonar_remedy_fetch", {"repo": "C:/r", "kinds": ["coverage", "duplication"]}
        )
        self.assertEqual(argv, ["fetch", "--repo", "C:/r", "--kinds", "coverage,duplication"])

    def test_build_argv_status(self):
        self.assertEqual(
            mcp.build_argv("sonar_remedy_status", {"state": "C:/q"}), ["status", "--state", "C:/q"]
        )

    def test_build_argv_run_flags(self):
        argv = mcp.build_argv(
            "sonar_remedy_run", {"state": "C:/q", "limit": 8, "resume": True, "execute": True}
        )
        self.assertEqual(argv, ["run", "--state", "C:/q", "--limit", "8", "--resume", "--execute"])

    def test_build_argv_autopilot(self):
        argv = mcp.build_argv(
            "sonar_remedy_autopilot",
            {"state": "C:/q", "repo": "C:/r", "limit": 3, "integrate": True, "execute": True},
        )
        self.assertEqual(
            argv,
            [
                "autopilot",
                "--state",
                "C:/q",
                "--repo",
                "C:/r",
                "--limit",
                "3",
                "--integrate",
                "--execute",
            ],
        )

    def test_build_argv_detect_checks(self):
        argv = mcp.build_argv("sonar_remedy_detect_checks", {"repo": "C:/r"})
        self.assertEqual(argv, ["detect-checks", "--repo", "C:/r"])

    def test_build_argv_unknown(self):
        with self.assertRaises(ValueError):
            mcp.build_argv("sonar_remedy_nope", {})

    def test_build_argv_configure_project(self):
        argv = mcp.build_argv(
            "sonar_remedy_configure_project",
            {
                "name": "mem",
                "sonar_url": "http://h:9000",
                "project_key": "PK",
                "repo_url": "https://github.com/o/r",
                "local_path": "C:/r",
                "worktree_root": "C:/wt",
                "main_branch": "feature/Sonar",
                "allow_http": True,
            },
        )
        self.assertIn("configure-project", argv)
        self.assertIn("--sonar-url", argv)
        self.assertIn("--allow-http", argv)
        self.assertIn("--name", argv)

    def test_build_argv_scan_suppressions(self):
        self.assertEqual(
            mcp.build_argv("sonar_remedy_scan_suppressions", {"repo": "C:/r"}),
            ["scan-suppressions", "--repo", "C:/r"],
        )

    def test_build_argv_scan_exclusions(self):
        self.assertEqual(
            mcp.build_argv("sonar_remedy_scan_exclusions", {"repo": "C:/r"}),
            ["scan-exclusions", "--repo", "C:/r"],
        )

    def test_build_argv_rules(self):
        self.assertEqual(
            mcp.build_argv("sonar_remedy_rules", {"action": "list"}), ["rules", "list"]
        )
        self.assertEqual(
            mcp.build_argv("sonar_remedy_rules", {"action": "allow", "rule": "ANGULAR.TS_IGNORE"}),
            ["rules", "allow", "ANGULAR.TS_IGNORE"],
        )

    def test_build_argv_report(self):
        self.assertEqual(
            mcp.build_argv("sonar_remedy_report", {"state": "C:/q"}),
            ["report", "--state", "C:/q"],
        )

    def test_build_argv_doctor(self):
        self.assertEqual(mcp.build_argv("sonar_remedy_doctor", {}), ["doctor"])
        self.assertEqual(
            mcp.build_argv("sonar_remedy_doctor", {"state": "C:/q", "repo": "C:/r", "fix": True}),
            ["doctor", "--state", "C:/q", "--repo", "C:/r", "--fix"],
        )

    def test_call_tool_returns_output(self):
        def fake_main(argv):
            print('{"status": "ok"}')
            return 0

        with mock.patch.object(sonar_remedy, "main", side_effect=fake_main):
            self.assertEqual(mcp.call_tool("sonar_remedy_projects", {}), '{"status": "ok"}')


class HandleTests(unittest.TestCase):
    def test_initialize(self):
        resp = mcp._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "sonar-remedy")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_tools_list(self):
        resp = mcp._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(resp["result"]["tools"]), len(mcp.TOOLS))

    def test_tools_call(self):
        with mock.patch.object(sonar_remedy, "main", return_value=0):
            resp = mcp._handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "sonar_remedy_projects", "arguments": {}},
                }
            )
        self.assertEqual(resp["id"], 3)
        self.assertFalse(resp["result"]["isError"])

    def test_notification_no_response(self):
        self.assertIsNone(mcp._handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


if __name__ == "__main__":
    unittest.main()
