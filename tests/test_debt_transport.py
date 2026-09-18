"""Synthetic native-shaped events and real local children, never model sessions."""

import base64
import ctypes
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import debt_queue as q
import debt_transport as t

PACK = Path(__file__).resolve().parents[1]


def lines(events):
    return b"".join(q.encoded(e) for e in events)


class TransportTests(unittest.TestCase):
    def opencode(self, text='{"status":"proposed"}'):
        return [
            {
                "type": "step_start",
                "sessionID": "s1",
                "part": {"type": "step-start", "messageID": "m1"},
            },
            {
                "type": "text",
                "sessionID": "s1",
                "part": {"type": "text", "messageID": "m1", "text": text, "time": {"end": 1}},
            },
            {
                "type": "step_finish",
                "sessionID": "s1",
                "part": {"type": "step-finish", "messageID": "m1", "reason": "stop"},
            },
        ]

    def codex(self):
        return [
            {"type": "thread.started", "thread_id": "s1"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "i1", "type": "agent_message", "text": '{"status":"proposed"}'},
            },
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
        ]

    def test_manual_available_native_execution_unavailable(self):
        self.assertEqual(t.capability("manual")["status"], "available")
        for provider in ("opencode", "codex", "claude", "copilot"):
            self.assertEqual(t.capability(provider)["status"], "unavailable")

    def test_opencode_exact_text_and_terminal_contract(self):
        self.assertEqual(
            t.parse_events("opencode", lines(self.opencode()), session_id="s1", exit_code=0),
            {"status": "proposed"},
        )

    def test_codex_exact_thread_and_turn_contract(self):
        self.assertEqual(
            t.parse_events("codex", lines(self.codex()), session_id="s1", exit_code=0),
            {"status": "proposed"},
        )

    def test_unverified_claude_and_copilot_envelopes_fail_closed(self):
        for provider in ("claude", "copilot"):
            with self.subTest(provider=provider), self.assertRaises(q.Blocked):
                t.parse_events(provider, b'{"result":"{}"}', session_id="s1", exit_code=0)

    def test_wrong_session_nonzero_partial_missing_terminal_and_tool_use_rejected(self):
        variants = [
            self.opencode()[:-1],
            self.opencode() + [{"type": "tool_use", "sessionID": "s1"}],
        ]
        partial = self.opencode()
        partial[-1]["part"]["reason"] = "length"
        variants.append(partial)
        for events in variants:
            with self.subTest(events=events), self.assertRaises(q.Blocked):
                t.parse_events("opencode", lines(events), session_id="s1", exit_code=0)
        for session, exit_code in [("other", 0), ("s1", 1)]:
            with self.subTest(session=session, exit_code=exit_code), self.assertRaises(q.Blocked):
                t.parse_events(
                    "opencode", lines(self.opencode()), session_id=session, exit_code=exit_code
                )

    def test_codex_error_tool_duplicate_messages_and_missing_terminal_rejected(self):
        events = self.codex()
        variants = [
            events[:-1],
            events + [{"type": "error"}],
            events[:2]
            + [{"type": "item.completed", "item": {"type": "command_execution"}}]
            + events[2:],
            events[:3] + [events[2]] + events[3:],
        ]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(q.Blocked):
                t.parse_events("codex", lines(value), session_id="s1", exit_code=0)

    def test_no_first_json_search_or_sensitive_output_persistence(self):
        for text in (
            'noise {"status":"proposed"}',
            "```json\n{}\n```",
            '{"secret":"fixture-private"}',
        ):
            with self.subTest(text=text), self.assertRaises(q.Blocked):
                t.parse_events(
                    "opencode",
                    lines(self.opencode(text)),
                    session_id="s1",
                    exit_code=0,
                    sensitive_values=("fixture-private",),
                )

    def test_oversized_transport_rejected(self):
        with self.assertRaises(q.Blocked):
            t.parse_events("opencode", b"x" * (1024 * 1024 + 1), session_id="s1", exit_code=0)

    def test_real_child_exit_and_stderr_is_not_returned(self):
        result = t.run_process(
            [
                sys.executable,
                "-B",
                "-c",
                'import sys; print("ok"); print("fixture-private",file=sys.stderr); sys.exit(3)',
            ],
            PACK,
        )
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["stdout"].strip(), b"ok")
        self.assertNotIn("fixture-private", str(result))

    def test_real_child_timeout_and_output_bounds(self):
        for command, reason, limit in [
            ("import time; time.sleep(30)", "process_timeout", 1024),
            ('print("x"*100000)', "process_output_limit", 64),
        ]:
            start = time.monotonic()
            result = t.run_process(
                [sys.executable, "-B", "-c", command], PACK, timeout=0.3, output_limit=limit
            )
            self.assertEqual(result["reason"], reason)
            self.assertLess(time.monotonic() - start, 5)
            self.assertLessEqual(len(result["stdout"]), limit)

    def test_process_environment_drops_sonar_and_other_credentials(self):
        code = 'import os; print(os.getenv("SONAR_TOKEN")); print(os.getenv("PRIVATE_API_KEY"))'
        with patch.dict(
            os.environ, {"SONAR_TOKEN": "fixture-private", "PRIVATE_API_KEY": "other-private"}
        ):
            result = t.run_process([sys.executable, "-B", "-c", code], PACK)
        self.assertEqual(result["stdout"].splitlines(), [b"None", b"None"])

    def test_process_environment_forwards_user_profile_folders_for_sdk_builds(self):
        # Windows-hosted SDK toolchains (e.g. dotnet/NuGet) resolve their per-user
        # package cache from USERPROFILE/APPDATA/LOCALAPPDATA; without them a
        # configured build check silently produces a different (incomplete) output
        # set, which then fails the post-check snapshot as "unexpected" writes.
        code = (
            'import os; print(os.getenv("USERPROFILE")); print(os.getenv("APPDATA")); '
            'print(os.getenv("LOCALAPPDATA")); print(os.getenv("SONAR_TOKEN"))'
        )
        with patch.dict(
            os.environ,
            {
                "USERPROFILE": r"C:\Users\fixture",
                "APPDATA": r"C:\Users\fixture\AppData\Roaming",
                "LOCALAPPDATA": r"C:\Users\fixture\AppData\Local",
                "SONAR_TOKEN": "fixture-private",
            },
        ):
            result = t.run_process([sys.executable, "-B", "-c", code], PACK)
        self.assertEqual(
            result["stdout"].splitlines(),
            [
                rb"C:\Users\fixture",
                rb"C:\Users\fixture\AppData\Roaming",
                rb"C:\Users\fixture\AppData\Local",
                b"None",
            ],
        )

    def test_timeout_terminates_descendant_not_only_parent(self):
        code = (
            'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            "print(p.pid,flush=True); time.sleep(30)"
        )
        result = t.run_process([sys.executable, "-B", "-c", code], PACK, timeout=0.5)
        self.assertEqual(result["reason"], "process_timeout")
        self.assertTrue(result["stdout"].strip())
        pid = int(result["stdout"].strip())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0)
            finally:
                kernel.CloseHandle(handle)

    def test_encoded_sensitive_values_are_rejected_not_redacted_into_edits(self):
        secret = "fixture/private+value"
        for value in (quote(secret, safe=""), base64.b64encode(secret.encode()).decode()):
            text = json.dumps({"secret": value})
            with self.subTest(value=value), self.assertRaises(q.Blocked):
                t.parse_events(
                    "opencode",
                    lines(self.opencode(text)),
                    session_id="s1",
                    exit_code=0,
                    sensitive_values=(secret,),
                )

    def test_stdin_empty_arguments_and_delayed_output_are_bounded(self):
        code = "import sys,time,json; time.sleep(.03); print(json.dumps([sys.argv[1:],sys.stdin.read()]))"
        result = t.run_process(
            [sys.executable, "-B", "-c", code, "", "a b"],
            PACK,
            timeout=2,
            input_data=b"bounded\ninput",
        )
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(json.loads(result["stdout"]), [["", "a b"], "bounded\ninput"])


class ClaudeProfileTests(unittest.TestCase):
    def profile(self):
        return {
            "version": 1,
            "name": "claude-bare-api-key-v1",
            "provider": "claude",
            "executable": sys.executable,
            "executable_sha256": t.executable_digest(sys.executable),
            "cli_version": "2.1.139",
            "model": "claude-sonnet-4-6",
            "destination": "https://api.anthropic.com",
            "auth_env": "ANTHROPIC_API_KEY",
            "timeout_seconds": 10,
            "max_budget_usd": 1,
        }

    def test_explicit_profile_conditionally_available_without_auth_inspection(self):
        with patch.dict(os.environ, {}, clear=True):
            result = t.capability("claude", self.profile())
        self.assertEqual(result["status"], "available")
        self.assertFalse(result.get("live_verified", False))

    def test_invalid_profile_never_authorizes_arbitrary_tools_auth_or_destination(self):
        for values in (
            {"auth_env": "SONAR_TOKEN"},
            {"destination": "https://other.example"},
            {"name": "general"},
            {"tools": ["Bash"]},
            {"executable_sha256": "0" * 64},
        ):
            with self.subTest(values=values):
                self.assertEqual(
                    t.capability("claude", dict(self.profile(), **values))["status"], "unavailable"
                )

    def test_fixed_factory_uses_only_restricted_native_argv_and_bounded_stdin(self):
        result = t.claude_invocation(
            self.profile(), {"job_id": "j1"}, PACK, "00000000-0000-4000-8000-000000000001"
        )
        self.assertIsNotNone(result)
        argv = result["argv"]
        for flag in (
            "--bare",
            "-p",
            "--tools",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--session-id",
        ):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--resume", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertEqual(json.loads(result["input_data"])["job"]["job_id"], "j1")

    def test_claude_exact_result_json_and_error_partial_multiple_tool_trace_rejection(self):
        envelope = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": "s1",
            "num_turns": 1,
            "stop_reason": "end_turn",
            "result": '{"status":"proposed"}',
        }
        try:
            actual = t.parse_events("claude", q.encoded(envelope), session_id="s1", exit_code=0)
        except q.Blocked:
            actual = None
        self.assertEqual(actual, {"status": "proposed"})
        for changes in (
            {"is_error": True},
            {"session_id": "wrong"},
            {"subtype": "error_max_turns"},
            {"stop_reason": "max_tokens"},
            {"num_turns": 2},
            {"tool_calls": [{}]},
            {"deferred_tool_use": {"name": "Bash"}},
            {"result": "junk"},
        ):
            with self.subTest(changes=changes), self.assertRaises(q.Blocked):
                t.parse_events(
                    "claude", q.encoded(dict(envelope, **changes)), session_id="s1", exit_code=0
                )
        with self.assertRaises(q.Blocked):
            t.parse_events("claude", q.encoded(envelope) * 2, session_id="s1", exit_code=0)
