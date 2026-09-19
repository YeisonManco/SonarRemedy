"""Versioned pre-push gate installed by `init`. Stdlib only.

The hook refuses the push when the project's declared gates fail, so a
committer (human, Copilot or agent) cannot land code with a red build or
suite. The logic lives here (unit-tested); the installed shell shim only
forwards to this module.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HOOK_VERSION = 1
HOOK_NAME = "pre-push"
MARKER = "# SonarRemedy pre-push gate"
HOOKS_FILE = ".sonarremedy-hooks.json"
TIMEOUT_SECONDS = 900


class Blocked(ValueError):
    """Refusal with an exact reason; recognized by name across modules."""


def _pack_dir() -> Path:
    return Path(__file__).resolve().parent


def hook_body() -> str:
    """Exact bytes installed as the pre-push hook (versioned for staleness checks)."""
    module = _pack_dir() / "sonar_hooks.py"
    return (
        "#!/bin/sh\n"
        f"{MARKER} v{HOOK_VERSION} (managed by `sonarremedy init`; do not edit).\n"
        f'exec python -B "{module}" pre-push "$(git rev-parse --show-toplevel)"\n'
    )


def _hooks_dir(target: str | Path) -> Path:
    return Path(os.path.abspath(target)) / ".git" / "hooks"


def hook_path(target: str | Path) -> Path:
    """Location of the pre-push hook for a checkout (may not exist)."""
    return _hooks_dir(target) / HOOK_NAME


def install(target: str | Path) -> dict[str, Any]:
    """Install the gate hook; never overwrite a foreign hook."""
    root = Path(os.path.abspath(target))
    if not (root / ".git").is_dir():
        return {"status": "skipped", "reason": "not_a_git_checkout"}
    hooks = _hooks_dir(root)
    hooks.mkdir(parents=True, exist_ok=True)
    path = hooks / HOOK_NAME
    body = hook_body()
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if existing == body:
            return {"status": "ok", "hook": str(path)}
        if MARKER not in existing:
            raise Blocked("foreign_hook: refusing to overwrite a non-SonarRemedy hook")
    path.write_text(body, encoding="utf-8")
    return {"status": "installed", "hook": str(path)}


def status(target: str | Path) -> dict[str, Any]:
    """Report the gate hook state without changing anything."""
    root = Path(os.path.abspath(target))
    if not (root / ".git").is_dir():
        return {"status": "not_a_repo"}
    path = hook_path(root)
    if not path.is_file():
        return {"status": "missing", "hook": str(path)}
    existing = path.read_text(encoding="utf-8")
    if MARKER not in existing:
        return {"status": "foreign", "hook": str(path)}
    if existing != hook_body():
        return {"status": "outdated", "hook": str(path), "version": HOOK_VERSION}
    return {"status": "ok", "hook": str(path), "version": HOOK_VERSION}


def _has_ruff() -> bool:
    if shutil.which("ruff") is not None:
        return True
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _run_gate(argv: list[str], root: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=TIMEOUT_SECONDS, cwd=root)
    except (OSError, subprocess.SubprocessError) as error:
        return {"status": "blocked", "reason": f"hook command failed to start: {argv}: {error}"}
    if completed.returncode != 0:
        return {
            "status": "blocked",
            "reason": f"hook command failed: {argv} (returncode {completed.returncode})",
            "fix": "fix the failing gate, then push again (never use --no-verify)",
        }
    return {"status": "ok"}


def _pack_gates(root: Path) -> dict[str, Any]:
    if not _has_ruff():
        return {
            "status": "blocked",
            "reason": "ruff is required for the pre-push gate",
            "fix": "python -m pip install ruff",
        }
    commands = [
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "ruff", "format", "--check", "."],
    ]
    for argv in commands:
        result = _run_gate(argv, root)
        if result["status"] != "ok":
            return result
    return {"status": "ok", "gates": len(commands)}


def run_gates(target: str | Path) -> dict[str, Any]:
    """Run the project's pre-push gates; fail closed with an exact reason."""
    root = Path(os.path.abspath(target))
    hooks_file = root / HOOKS_FILE
    if hooks_file.is_file():
        try:
            declared = json.loads(hooks_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"status": "blocked", "reason": f"invalid {HOOKS_FILE}", "fix": "fix the JSON"}
        commands = declared.get("pre-push", [])
        if not isinstance(commands, list) or not all(isinstance(c, list) for c in commands):
            return {
                "status": "blocked",
                "reason": f"invalid {HOOKS_FILE}: pre-push must be a list of argv lists",
                "fix": "fix the JSON",
            }
        for argv in commands:
            result = _run_gate([str(part) for part in argv], root)
            if result["status"] != "ok":
                return result
        return {"status": "ok", "gates": len(commands)}
    if (root / "sonar_remedy.py").is_file() and (root / "tests").is_dir():
        return _pack_gates(root)
    if (root / ".sonarremedy").is_dir():
        return {
            "status": "blocked",
            "reason": "hooks_not_configured",
            "fix": f'declare gates in {HOOKS_FILE}: {{"pre-push": [[...argv...]]}}',
        }
    return {"status": "skipped", "reason": "no gates declared and no pack markers"}


def main(argv: list[str] | None = None) -> int:
    """Entry point used by the installed shell shim: `sonar_hooks.py pre-push <root>`."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "pre-push":
        print(json.dumps({"status": "blocked", "reason": "usage: sonar_hooks.py pre-push <root>"}))
        return 2
    result = run_gates(args[1])
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in ("ok", "skipped") else 2


if __name__ == "__main__":
    raise SystemExit(main())
