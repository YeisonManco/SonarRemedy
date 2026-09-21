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
        "description": "Fetch Sonar findings for a project into an export (chunked if over budget). Use `kinds` to pull only some categories: smells, security, hotspots, coverage, duplication (default all).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "repo": {"type": "string"},
                "output": {"type": "string"},
                "kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["smells", "security", "hotspots", "coverage", "duplication"],
                    },
                },
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
        "name": "sonar_remedy_autopilot",
        "description": "Deterministic harness: setup-gate, slice, run, configure-gate, integrate, status. Reports its phase with the exact next command; the model only fills bounded proposals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "repo": {"type": "string"},
                "export": {"type": "string"},
                "limit": {"type": "integer"},
                "integrate": {"type": "boolean"},
                "execute": {"type": "boolean"},
            },
            "required": ["state"],
        },
    },
    {
        "name": "sonar_remedy_detect_checks",
        "description": "Inspect a checkout and draft its checks.json (read-only). Present the draft plus missing to the human and ask for the judgments; never invent exe paths or hashes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "state": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "sonar_remedy_recover",
        "description": "Loop the debt-recovery cycle until done or impossible: fetch, slice, run, configure, integrate, and re-analyze + re-fetch on re_scan_required.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {"type": "string"},
                "checks": {"type": "string"},
                "approve_checks_sha256": {"type": "string"},
                "limit": {"type": "integer"},
                "max_cycles": {"type": "integer"},
                "execute": {"type": "boolean"},
            },
            "required": ["state", "checks", "approve_checks_sha256"],
        },
    },
    {
        "name": "sonar_remedy_analyze",
        "description": "Run the local pipeline to regenerate+publish Sonar results (uses the pack's built-in script when `script` is omitted).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "script": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {"type": "string"},
                "execute": {"type": "boolean"},
            },
            "required": [],
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
        "description": "Save a project's Sonar/repo config non-interactively. The token is NOT stored here — the user sets the env var (SONAR_TOKEN by default, or a custom name via token_env) separately (masked).",
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
                "token_env": {"type": "string"},
                "pat_env": {"type": "string"},
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
    {
        "name": "sonar_remedy_scan_suppressions",
        "description": "Detect code-level suppressions that may evade Sonar (NOSONAR, pragma, noqa, ...), tiered certain vs ambiguous.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "sonar_remedy_scan_exclusions",
        "description": "SCAN the code to DETECT Sonar exclusions/suppressions (NOSONAR, @ts-ignore, #pragma, NoWarn, coverage exclusions) by language and category. Use this to FIND exclusions in the repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
            },
        },
    },
    {
        "name": "sonar_remedy_rules",
        "description": "Show or edit the CONFIGURED exclusion whitelist/blacklist (list, allow=whitelist, block=blacklist, remove). This does NOT scan code — use sonar_remedy_scan_exclusions to detect exclusions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "allow", "block", "remove"]},
                "rule": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "sonar_remedy_doctor",
        "description": "Diagnose the SonarRemedy setup and a queue's identity (root/branch/revision), reporting each mismatch with the exact fix command. With fix=true it also applies the SAFE repairs (re-run init if the project setup is outdated or init files are missing). Use this whenever a command fails with a blocked reason you do not understand.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "repo": {"type": "string"},
                "fix": {"type": "boolean"},
            },
        },
    },
    {
        "name": "sonar_remedy_report",
        "description": "List applied fixes and the human follow-up each requires.",
        "inputSchema": {
            "type": "object",
            "properties": {"state": {"type": "string"}},
            "required": ["state"],
        },
    },
    {
        "name": "sonar_remedy_document",
        "description": "Regenerate the deterministic audit-trail progress.json + report.json (every job, entry and attempt, including the typesafe_hotspot_risk advisory field when present). Default is dry-run; execute=true writes the files into the queue's state directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "execute": {"type": "boolean"},
            },
            "required": ["state"],
        },
    },
    {
        "name": "sonar_remedy_defer",
        "description": "Defer pending/proposed work without changing target files. Default is dry-run; execute=true records the deferral.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "job": {"type": "string"},
                "reason": {"type": "string"},
                "execute": {"type": "boolean"},
            },
            "required": ["state", "job", "reason"],
        },
    },
    {
        "name": "sonar_remedy_reconcile",
        "description": "Resolve an expired lease; never automatically retries. Requires a saved claim JSON (receipt) with the exact lease identity, and an explicit effects assessment (none|unknown). Default is dry-run; execute=true resolves the lease.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "receipt": {"type": "string"},
                "effects": {"type": "string", "enum": ["none", "unknown"]},
                "execute": {"type": "boolean"},
            },
            "required": ["state", "receipt", "effects"],
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
        argv = g + ["fetch"] + _opt("--repo", a.get("repo")) + _opt("--output", a.get("output"))
        kinds = a.get("kinds")
        if kinds:
            argv += ["--kinds", ",".join(kinds)]
        return argv
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
    if name == "sonar_remedy_autopilot":
        return (
            g
            + ["autopilot", "--state", a["state"]]
            + _opt("--repo", a.get("repo"))
            + _opt("--export", a.get("export"))
            + _opt("--limit", a.get("limit"))
            + (["--integrate"] if a.get("integrate") else [])
            + (["--execute"] if a.get("execute") else [])
        )
    if name == "sonar_remedy_detect_checks":
        return (
            g + ["detect-checks"] + _opt("--repo", a.get("repo")) + _opt("--state", a.get("state"))
        )
    if name == "sonar_remedy_recover":
        return (
            g
            + ["recover", "--state", a["state"]]
            + _opt("--repo", a.get("repo"))
            + _opt("--branch", a.get("branch"))
            + ["--checks", a["checks"], "--approve-checks-sha256", a["approve_checks_sha256"]]
            + _opt("--limit", a.get("limit"))
            + _opt("--max-cycles", a.get("max_cycles"))
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
            + _opt("--token-env", a.get("token_env"))
            + _opt("--pat-env", a.get("pat_env"))
            + (["--allow-http"] if a.get("allow_http") else [])
        )
    if name == "sonar_remedy_scan_suppressions":
        return g + ["scan-suppressions"] + _opt("--repo", a.get("repo"))
    if name == "sonar_remedy_scan_exclusions":
        return g + ["scan-exclusions"] + _opt("--repo", a.get("repo"))
    if name == "sonar_remedy_rules":
        argv = g + ["rules", a["action"]]
        if a.get("rule"):
            argv.append(a["rule"])
        return argv
    if name == "sonar_remedy_doctor":
        argv = g + ["doctor"]
        argv += _opt("--state", a.get("state"))
        argv += _opt("--repo", a.get("repo"))
        if a.get("fix"):
            argv.append("--fix")
        return argv
    if name == "sonar_remedy_report":
        return g + ["report", "--state", a["state"]]
    if name == "sonar_remedy_document":
        return g + ["document", "--state", a["state"]] + (["--execute"] if a.get("execute") else [])
    if name == "sonar_remedy_defer":
        return (
            g
            + ["defer", "--state", a["state"], "--job", a["job"], "--reason", a["reason"]]
            + (["--execute"] if a.get("execute") else [])
        )
    if name == "sonar_remedy_reconcile":
        return (
            g
            + [
                "reconcile",
                "--state",
                a["state"],
                "--receipt",
                a["receipt"],
                "--effects",
                a["effects"],
            ]
            + (["--execute"] if a.get("execute") else [])
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
                "serverInfo": {"name": "sonar-remedy", "version": sonar_remedy.__version__},
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
