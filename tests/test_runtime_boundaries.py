"""Real local process/loopback fixtures, never a live Sonar endpoint."""

import io
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import sonar_client as c
import sonar_local as s
from sonar_gateway import Gateway

PACK = Path(__file__).resolve().parents[1]


class RuntimeBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=PACK / ".debt-state")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_real_process_redacts_and_returns_failure_code(self):
        log = self.root / "process.log"
        rc = s.run_process(
            [sys.executable, "-c", 'print("fixture-private"); raise SystemExit(3)'],
            self.root,
            s.child_environment(),
            10,
            log,
            s.redactor("fixture-private"),
        )
        self.assertEqual(rc, 3)
        self.assertEqual(log.read_text().strip(), "[REDACTED]")

    def test_real_process_timeout_is_bounded(self):
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            s.run_process(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                self.root,
                s.child_environment(),
                0.1,
                self.root / "timeout.log",
                s.redactor("fixture"),
            )
        self.assertLess(time.monotonic() - start, 12)

    def test_gateway_forwards_only_trusted_upstream_and_blocks_redirect(self):
        class FakeClient:
            timeout = 2
            paths = []

            def request(inner, path, *args, **kwargs):
                inner.paths.append(path)
                if path == "redirect":
                    raise c.Blocked("redirect refused")
                return b'{"status":"UP"}', "application/json"

        client = FakeClient()
        with Gateway(client) as gateway:
            with urlopen(gateway.url + "/api/system/status", timeout=2) as response:
                self.assertEqual(json.load(response)["status"], "UP")
            with self.assertRaises(HTTPError) as raised:
                urlopen(gateway.url + "/redirect", timeout=2)
            self.assertEqual(raised.exception.code, 502)
            raised.exception.close()
            with self.assertRaises(HTTPError) as raised:
                urlopen("http://" + gateway.host + "/api/system/status", timeout=2)
            self.assertEqual(raised.exception.code, 403)
            raised.exception.close()
        self.assertEqual(client.paths, ["api/system/status", "redirect"])

    def test_http_fixture_observes_bearer_only_at_configured_endpoint(self):
        client = c.Client("https://sonar.example.test/root", "fixture-private")

        class Response(io.BytesIO):
            headers = {"Content-Type": "application/json"}

            def geturl(inner):
                return "https://sonar.example.test/root/api/ce/task?id=one"

        class Opener:
            def open(inner, request, timeout):
                self.assertEqual(
                    request.full_url, "https://sonar.example.test/root/api/ce/task?id=one"
                )
                self.assertEqual(request.headers["Authorization"], "Bearer fixture-private")
                return Response(b'{"task": {"id": "one"}}')

        client.opener = Opener()
        self.assertEqual(client.get("api/ce/task", id="one")["task"]["id"], "one")

    def test_tls_verification_and_redirect_handler_are_enabled(self):
        client = c.Client("https://sonar.example.test", "fixture")
        handlers = client.opener.handlers
        tls = next(h for h in handlers if hasattr(h, "_context"))
        self.assertTrue(tls._context.check_hostname)
        self.assertEqual(tls._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(any(isinstance(h, c.NoRedirect) for h in handlers))

    def test_snapshot_binds_dirty_bytes_untracked_and_diff_without_exposing_content(self):
        (self.root / "a.cs").write_text("private source")
        (self.root / "new.cs").write_text("new source")
        (self.root / ".debt-scan.lock").write_text("123")

        def git(argv, **kwargs):
            command = argv[3]
            output = (
                str(self.root).encode()
                if argv[3:] == ["rev-parse", "--show-toplevel"]
                else b"abc123"
                if command == "rev-parse"
                else b"opaque diff"
                if command == "diff"
                else b"a.cs\0new.cs\0.debt-scan.lock\0"
            )
            return subprocess.CompletedProcess(argv, 0, output, b"")

        with patch.object(s.subprocess, "run", side_effect=git):
            first = s.snapshot(self.root)
            (self.root / ".debt-scan.lock").write_text("456")
            self.assertEqual(first, s.snapshot(self.root))
            (self.root / "new.cs").write_text("changed source")
            second = s.snapshot(self.root)
        self.assertNotEqual(first["digest"], second["digest"])
        self.assertNotIn("private source", json.dumps(first))

    def test_real_cli_dry_run_and_no_skip_switch(self):
        (self.root / "App.sln").write_text("fixture")
        config = {
            "adapter": "dotnet",
            "repo": str(self.root),
            "solution": "App.sln",
            "project": "fixture",
            "branch": "feature",
            "url": "https://sonar.example.test",
            "trusted_url": "https://sonar.example.test",
        }
        path = self.root / "config.json"
        path.write_text(json.dumps(config))
        env = dict(os.environ, SONAR_TOKEN="fixture-private")
        argv = [sys.executable, "-B", str(PACK / "sonar_local.py"), str(path)]
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["status"], "dry-run")
        self.assertNotIn("fixture-private", result.stdout + result.stderr)
        result = subprocess.run(argv + ["--skip-tests"], env=env, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
