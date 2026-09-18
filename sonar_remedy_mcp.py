"""Minimal stdlib MCP server exposing the sonar-remedy CLI as tools.

Each tool maps to a `sonar_remedy` subcommand. When you add or change a subcommand
in sonar_remedy.py, you MUST update TOOLS + build_argv here (see CONTRIBUTING.md).
"""

import contextlib
import io
import json
import sys
from typing import Any

import sonar_remedy

TOOLS = [
    {
        "name": "sonar_remedy_fetch",
        "description": "Fetch Sonar issues for a project into an export (chunked if over budget).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "repo": {"type": "string"},
                "output": {"type": "string"},
            },
        },
    },
    {
        "name": "sonar_remedy_slice",
        "description": "Create a durable queue from an export.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "repo": {"type": "string"},
                "export": {"type": "string"},
                "state": {"type": "string"},
                "execute": {"type": "boolean"},
            },
            "required": ["export", "state"],
        },
    },
    {
        "name": "sonar_remedy_run",
        "description": "Lease/process a bounded manual proposal batch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "limit": {"type": "integer"},
                "resume": {"type": "boolean"},
                "execute": {"type": "boolean"},
            },
            "required": ["state"],
        },
    },
    {
        "name": "sonar_remedy_status",
        "description": "Report the queue's next action and ETA.",
        "inputSchema": {
            "type": "object",
            "properties": {"state": {"type": "string"}},
            "required": ["state"],
        },
    },
    {
        "name": "sonar_remedy_progress",
        "description": "Write a human-readable progress file.",
        "inputSchema": {
            "type": "object",
            "properties": {"state": {"type": "string"}},
            "required": ["state"],
        },
    },
    {
        "name": "sonar_remedy_schedule",
        "description": "Show the parallel/serial plan for pending jobs.",
        "inputSchema": {
            "type": "object",
            "properties": {"state": {"type": "string"}},
            "required": ["state"],
        },
    },
    {
        "name": "sonar_remedy_run_all",
        "description": "Fetch + slice every chunk into its own queue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "repo": {"type": "string"},
                "state": {"type": "string"},
                "execute": {"type": "boolean"},
            },
            "required": ["state"],
        },
    },
    {
        "name": "sonar_remedy_analyze",
        "description": "Run the local pipeline to regenerate+publish Sonar results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "script": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {"type": "string"},
                "execute": {"type": "boolean"},
            },
            "required": ["script"],
        },
    },
    {
        "name": "sonar_remedy_integrate",
        "description": "Serially apply a recorded proposal and run bound checks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "job": {"type": "string"},
                "execute": {"type": "boolean"},
            },
            "required": ["state", "job"],
        },
    },
    {
        "name": "sonar_remedy_configure",
        "description": "Bind reviewed check commands and the current target snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "checks": {"type": "string"},
                "approve_checks_sha256": {"type": "string"},
                "execute": {"type": "boolean"},
            },
            "required": ["state", "checks"],
        },
    },
    {
        "name": "sonar_remedy_projects",
        "description": "List saved project configs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sonar_remedy_configure_project",
        "description": "Save a project's Sonar/repo config non-interactively. The token is NOT stored here — the user sets SONAR_TOKEN separately (masked).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "sonar_url": {"type": "string"},
                "project_key": {"type": "string"},
                "repo_url": {"type": "string"},
                "local_path": {"type": "string"},
                "worktree_root": {"type": "string"},
                "main_branch": {"type": "string"},
                "provider": {"type": "string"},
                "allow_http": {"type": "boolean"},
            },
            "required": [
                "name",
                "sonar_url",
                "project_key",
                "repo_url",
                "local_path",
                "worktree_root",
            ],
        },
    },
]


def _global(a: dict[str, Any]) -> list[str]:
    flags = []
    if a.get("project"):
        flags += ["--project", a["project"]]
    if a.get("config"):
        flags += ["--config", a["config"]]
    return flags


def _opt(flag: str, value: Any) -> list[str]:
    return [flag, str(value)] if value is not None else []


def build_argv(name: str, arguments: dict[str, Any] | None) -> list[str]:
    a = arguments or {}
    g = _global(a)
    if name == "sonar_remedy_fetch":
        return g + ["fetch"] + _opt("--repo", a.get("repo")) + _opt("--output", a.get("output"))
    if name == "sonar_remedy_slice":
        return (
            g
            + ["slice"]
            + _opt("--repo", a.get("repo"))
            + _opt("--export", a.get("export"))
            + _opt("--state", a.get("state"))
            + (["--execute"] if a.get("execute") else [])
        )
    if name == "sonar_remedy_run":
        return (
            g
            + ["run", "--state", a["state"]]
            + _opt("--limit", a.get("limit"))
            + (["--resume"] if a.get("resume") else [])
            + (["--execute"] if a.get("execute") else [])
        )
    if name == "sonar_remedy_status":
        return g + ["status", "--state", a["state"]]
    if name == "sonar_remedy_progress":
        return g + ["progress", "--state", a["state"]]
    if name == "sonar_remedy_schedule":
        return g + ["schedule", "--state", a["state"]]
    if name == "sonar_remedy_run_all":
        return (
            g
            + ["run-all", "--state", a["state"]]
            + _opt("--repo", a.get("repo"))
            + (["--execute"] if a.get("execute") else [])
        )
    if name == "sonar_remedy_analyze":
        return (
            g
            + ["analyze", "--script", a["script"]]
            + _opt("--repo", a.get("repo"))
            + _opt("--branch", a.get("branch"))
            + (["--execute"] if a.get("execute") else [])
        )
    if name == "sonar_remedy_integrate":
        return (
            g
            + ["integrate", "--state", a["state"], "--job", a["job"]]
            + (["--execute"] if a.get("execute") else [])
        )
    if name == "sonar_remedy_configure":
        return (
            g
            + ["configure", "--state", a["state"], "--checks", a["checks"]]
            + _opt("--approve-checks-sha256", a.get("approve_checks_sha256"))
            + (["--execute"] if a.get("execute") else [])
        )
    if name == "sonar_remedy_projects":
        return g + ["projects"]
    if name == "sonar_remedy_configure_project":
        return (
            g
            + [
                "configure-project",
                "--name",
                a["name"],
                "--sonar-url",
                a["sonar_url"],
                "--project-key",
                a["project_key"],
                "--repo-url",
                a["repo_url"],
                "--local-path",
                a["local_path"],
                "--worktree-root",
                a["worktree_root"],
            ]
            + _opt("--main-branch", a.get("main_branch"))
            + _opt("--provider", a.get("provider"))
            + (["--allow-http"] if a.get("allow_http") else [])
        )
    raise ValueError(f"unknown tool: {name}")


def call_tool(name: str, arguments: dict[str, Any] | None) -> str:
    """Run the tool and return sonar_remedy's printed JSON (or a compact exit note)."""
    argv = build_argv(name, arguments)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = sonar_remedy.main(argv)
    output = buf.getvalue().strip()
    return output if output else json.dumps({"exit": code})


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sonar-remedy", "version": "0.1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params", {})
        try:
            text = call_tool(params.get("name"), params.get("arguments"))
            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {"content": [{"type": "text", "text": text}], "isError": False},
            }
        except Exception as error:
            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {"content": [{"type": "text", "text": str(error)}], "isError": True},
            }
    if "id" not in message:
        return None  # notification; no response
    return {
        "jsonrpc": "2.0",
        "id": message.get("id"),
        "error": {"code": -32601, "message": "method not found"},
    }


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        response = _handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve()
