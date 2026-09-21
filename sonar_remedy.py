"""SonarRemedy facade: drive the Sonar debt pipeline from persisted config."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path
from typing import Any

import sonar_fetch
import sonar_hooks
import sonar_remedy_config as rc

try:
    __version__ = _dist_version("sonarremedy")
except PackageNotFoundError:  # running from source without an install
    __version__ = "dev"


def build_fetch_config(
    rcfg: dict[str, Any], repo: str, kinds: list[str] | None = None
) -> sonar_fetch.Config:
    """Map a persisted SonarRemedy config to sonar_fetch's API Config."""
    url = rcfg["sonar"]["url"]
    raw = {
        "adapter": "api",
        "repo": repo,
        "url": url,
        "trusted_url": url,
        "project": rcfg["sonar"]["project_key"],
        "branch": rcfg["repository"]["main_branch"],
        "allow_http": rcfg["sonar"].get("allow_http", False),
    }
    if kinds is not None:
        raw["kinds"] = kinds
    return sonar_fetch.Config(raw)


def _default_output(project: str | None = None) -> str:
    return os.path.join(os.path.expanduser("~"), ".sonar-remedy", "runs", project or "default")


def _load_config(args: argparse.Namespace) -> dict[str, Any]:
    if args.config:
        return rc.load(args.config)
    if args.project:
        return rc.load_project(args.project)
    # No explicit project/config: try to bind the current checkout to exactly one
    # saved project before falling back to the default config. This prevents the
    # agent from silently running against the wrong project when it stands inside
    # a checkout that matches a saved project.
    resolved = rc.resolve_project_for_checkout(os.getcwd())
    if resolved["status"] == "ok":
        return rc.load_project(resolved["project"])
    if resolved["status"] == "ambiguous":
        names = ", ".join(m["name"] for m in resolved["matches"])
        raise rc.ConfigError(
            "multiple saved projects match this checkout (" + names + "); "
            "pass --project to disambiguate"
        )
    return rc.load(rc.default_config_path())


def _resolve_project_name(args: argparse.Namespace) -> str | None:
    """Return the project name this run targets (for the queue index), or None."""
    if getattr(args, "project", None):
        return args.project
    if getattr(args, "config", None):
        return None
    resolved = rc.resolve_project_for_checkout(os.getcwd())
    return resolved["project"] if resolved["status"] == "ok" else None


INSTR_SECTION_START = "<!-- SonarRemedy:start -->"
INSTR_SECTION_END = "<!-- SonarRemedy:end -->"


def _merge_copilot_instructions(target: str, section: str) -> str:
    """Merge the SonarRemedy section into the existing copilot-instructions.

    Preserves the user's own content and only REPLACES our delimited section
    (between the start/end markers) on re-init.
    """
    path = os.path.join(os.path.abspath(target), ".github", "copilot-instructions.md")
    existing = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            existing = handle.read()
    pattern = re.compile(
        re.escape(INSTR_SECTION_START) + r".*?" + re.escape(INSTR_SECTION_END) + r"\n?",
        re.DOTALL,
    )
    existing = pattern.sub("", existing).strip()
    merged = INSTR_SECTION_START + "\n" + section.strip() + "\n" + INSTR_SECTION_END
    if existing:
        merged = existing + "\n\n" + merged
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(merged + "\n")
    return path


def _write_init_files(target: str) -> tuple[str, str, str]:
    """Write .vscode/mcp.json + the Copilot pointer + the full instructions."""
    pack = os.path.dirname(os.path.abspath(__file__))
    vscode_dir = os.path.join(target, ".vscode")
    os.makedirs(vscode_dir, exist_ok=True)
    mcp_path = os.path.join(vscode_dir, "mcp.json")
    mcp_config = {
        "servers": {
            "sonar-remedy": {
                "type": "stdio",
                "command": "python",
                "args": ["-B", os.path.join(pack, "sonar_remedy_mcp.py")],
            }
        }
    }
    with open(mcp_path, "w", encoding="utf-8") as handle:
        json.dump(mcp_config, handle, indent=2)

    def _copy(source_name: str, dest_path: str, fallback: str) -> str:
        source = os.path.join(pack, "host-agents", source_name)
        if os.path.isfile(source):
            with open(source, encoding="utf-8") as handle:
                content = handle.read()
        else:
            content = fallback
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return dest_path

    pointer_source = os.path.join(pack, "host-agents", "copilot-instructions.md")
    if os.path.isfile(pointer_source):
        with open(pointer_source, encoding="utf-8") as handle:
            pointer_section = handle.read()
    else:
        pointer_section = (
            "# SonarRemedy\n\n"
            "Use the `sonar_remedy_*` MCP tools; read `.github/sonarremedy-instructions.md`.\n"
        )
    # Merge (preserve the user's own instructions) rather than overwrite.
    pointer_path = _merge_copilot_instructions(target, pointer_section)
    # The full instructions live under .github/ (NOT .sonarremedy/, which is
    # gitignored): Copilot's file search skips gitignored paths, so a pointer to
    # a file inside .sonarremedy/ would be unreadable.
    full_path = _copy(
        "sonarremedy-instructions.md",
        os.path.join(target, ".github", "sonarremedy-instructions.md"),
        "# SonarRemedy\n\nUse the `sonar_remedy_*` MCP tools to recover Sonar debt.\n",
    )
    return mcp_path, pointer_path, full_path


def _sonarremedy_dir(target: str) -> str:
    return os.path.join(os.path.abspath(target), ".sonarremedy")


def _worktree_root(target: str) -> str:
    """Sibling folder for per-branch worktrees (outside the repo)."""
    absolute = os.path.abspath(target)
    return os.path.join(os.path.dirname(absolute), os.path.basename(absolute) + "-remedy-wtrees")


_GITIGNORE_ENTRIES = (".sonarremedy/", ".vscode/mcp.json")


def _ensure_gitignore(target: str) -> list[str]:
    """Append missing local-setup entries to .gitignore; returns what was added."""
    gitignore = os.path.join(os.path.abspath(target), ".gitignore")
    existing = ""
    if os.path.isfile(gitignore):
        with open(gitignore, encoding="utf-8") as handle:
            existing = handle.read()
    missing = [entry for entry in _GITIGNORE_ENTRIES if entry not in existing]
    if not missing:
        return []
    with open(gitignore, "a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        for entry in missing:
            handle.write(entry + "\n")
    return missing


def _version_file(target: str) -> str:
    return os.path.join(_sonarremedy_dir(target), "version.json")


def _read_pack_version(target: str) -> str | None:
    path = _version_file(target)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle).get("version")
        except (ValueError, OSError):
            return None
    return None


def _track_version(target: str) -> dict[str, Any]:
    """Record the current pack version; report whether it changed since the last init."""
    previous = _read_pack_version(target)
    path = _version_file(target)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"version": __version__}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return {
        "previous": previous,
        "current": __version__,
        "updated": previous is not None and previous != __version__,
    }


def _missing_init_files(target: str) -> list[str]:
    """List init-written files absent in target (worktree-safe check)."""
    base = os.path.abspath(target)
    missing: list[str] = []
    candidates = [
        os.path.join(".vscode", "mcp.json"),
        os.path.join(".github", "sonarremedy-instructions.md"),
    ]
    for rel in candidates:
        if not os.path.isfile(os.path.join(base, rel)):
            missing.append(rel.replace(os.sep, "/"))
    pointer_rel = os.path.join(".github", "copilot-instructions.md")
    pointer_abs = os.path.join(base, pointer_rel)
    try:
        with open(pointer_abs, encoding="utf-8") as handle:
            pointer_content = handle.read()
    except OSError:
        pointer_content = ""
    if INSTR_SECTION_START not in pointer_content:
        missing.append(pointer_rel.replace(os.sep, "/"))
    return missing


def _check(target: str) -> dict[str, Any]:
    """Compare the project's recorded pack version with the installed one."""
    previous = _read_pack_version(target)
    if previous is None:
        return {
            "status": "not_initialized",
            "current": __version__,
            "hint": "run `sonarremedy init` from the project root",
        }
    if previous != __version__:
        return {
            "status": "outdated",
            "previous": previous,
            "current": __version__,
            "hint": "SonarRemedy was updated; run `sonarremedy init` from the project root",
        }
    missing = _missing_init_files(target)
    if missing:
        return {
            "status": "not_initialized",
            "current": __version__,
            "missing": missing,
            "hint": f"run `sonarremedy init --dir {os.path.abspath(target)}`",
        }
    return {"status": "up_to_date", "version": __version__}


def _doctor(
    repo: str | None, state: str | None, project_dir: str, *, fix: bool = False
) -> dict[str, Any]:
    """Diagnose the setup + a queue's identity; return per-check status and fixes.

    With ``fix=True`` it also applies the SAFE repairs (re-run ``init`` when the
    project's recorded version is behind the pack or init files are missing)
    and reports them.
    """
    checks: list[dict[str, Any]] = []

    resolved = rc.resolve_project_for_checkout(repo or project_dir)
    if resolved["status"] == "ok":
        checks.append({"name": "project", "status": "ok", "detail": resolved["project"]})
    elif resolved["status"] == "ambiguous":
        names = ", ".join(m["name"] for m in resolved["matches"])
        checks.append(
            {
                "name": "project",
                "status": "warning",
                "detail": "ambiguous: " + names,
                "fix": "pass --project to disambiguate",
            }
        )
    else:
        checks.append(
            {
                "name": "project",
                "status": "warning",
                "detail": "no saved project matches this checkout",
                "fix": "run `sonarremedy configure-project` or pass --project",
            }
        )

    collisions = rc.list_project_collisions()
    if collisions:
        detail = "; ".join(
            f"{c['project_a']}<->{c['project_b']} ({c['reason']})" for c in collisions
        )
        checks.append(
            {
                "name": "project_collisions",
                "status": "warning",
                "detail": detail,
                "fix": "give the projects distinct local_path / repository.url values",
            }
        )

    creds = rc.detect_remote_credentials(repo or project_dir)
    if creds["status"] == "warning":
        checks.append(
            {
                "name": "remote_credentials",
                "status": "warning",
                "detail": creds["detail"],
                "fix": creds["fix"],
            }
        )
    elif creds["status"] == "ok":
        checks.append({"name": "remote_credentials", "status": "ok", "detail": creds["detail"]})
    else:
        checks.append(
            {"name": "remote_credentials", "status": "info", "detail": "no origin remote"}
        )

    previous = _read_pack_version(project_dir)
    if previous is None:
        checks.append(
            {
                "name": "version",
                "status": "warning",
                "detail": "project not initialized",
                "fix": "run `sonarremedy init`",
            }
        )
    elif previous != __version__:
        checks.append(
            {
                "name": "version",
                "status": "warning",
                "detail": f"project={previous}, pack={__version__}",
                "fix": "run `sonarremedy init`",
            }
        )
    else:
        checks.append({"name": "version", "status": "ok", "detail": __version__})

    missing = _missing_init_files(project_dir)
    if missing:
        checks.append(
            {
                "name": "init_files",
                "status": "warning",
                "detail": f"missing: {', '.join(missing)}",
                "fix": f"run `sonarremedy init --dir {os.path.abspath(project_dir)}`",
            }
        )

    hook_state = sonar_hooks.status(project_dir)
    if hook_state["status"] == "ok":
        checks.append({"name": "git_hooks", "status": "ok", "detail": f"v{hook_state['version']}"})
    elif hook_state["status"] == "not_a_repo":
        checks.append(
            {"name": "git_hooks", "status": "info", "detail": "not a git checkout; no hook needed"}
        )
    else:
        checks.append(
            {
                "name": "git_hooks",
                "status": "warning",
                "detail": hook_state["status"],
                "fix": f"run `sonarremedy init --dir {os.path.abspath(project_dir)}`",
            }
        )

    if repo:
        import debt_executor

        barrier = debt_executor.release_barrier(repo, execute=False)
        if barrier["status"] == "ok":
            checks.append({"name": "barrier", "status": "ok", "detail": "no active barrier"})
        elif barrier.get("reason") == "verified_orphan_ready_to_release":
            checks.append(
                {
                    "name": "barrier",
                    "status": "warning",
                    "detail": "orphaned integration barrier, tree verified unchanged",
                    "fix": "run `sonarremedy doctor --repo <path> --fix` to release it safely",
                }
            )
        else:
            checks.append(
                {
                    "name": "barrier",
                    "status": "warning",
                    "detail": barrier.get("reason", "unknown"),
                    "fix": barrier.get("fix", "inspect the barrier manually"),
                }
            )

    if state:
        registered = rc.project_for_queue(state)
        if registered:
            checks.append({"name": "queue_project", "status": "ok", "detail": registered})
        else:
            checks.append(
                {"name": "queue_project", "status": "info", "detail": "not in the queue index"}
            )

        import debt_queue

        try:
            work = debt_queue.Queue(state)
            binding = work.identity()
            if repo:
                actual = debt_queue.git_identity(repo)
                fixes = {
                    "root": (
                        "the queue is bound to a different checkout; run `run` with "
                        f"--repo {binding['root']} (or re-slice pointing at {repo})"
                    ),
                    "branch": (
                        f"check out the analyzed branch: git -C {binding['root']} "
                        f"checkout {binding['branch']} (or re-slice with the branch you have)"
                    ),
                    "revision": ("the repo moved past the analyzed revision; re-run fetch + slice"),
                }
                for key in ("root", "branch", "revision"):
                    if actual.get(key) != binding[key]:
                        checks.append(
                            {
                                "name": f"queue_identity.{key}",
                                "status": "error",
                                "detail": f"expected={binding[key]!r}, actual={actual.get(key)!r}",
                                "fix": fixes[key],
                            }
                        )
                    else:
                        checks.append(
                            {
                                "name": f"queue_identity.{key}",
                                "status": "ok",
                                "detail": binding[key],
                            }
                        )
            else:
                checks.append(
                    {
                        "name": "queue",
                        "status": "info",
                        "detail": (
                            f"bound to {binding['root']} @ {binding['branch']} "
                            f"({binding['revision'][:12]})"
                        ),
                    }
                )
        except Exception as error:
            checks.append(
                {
                    "name": "queue",
                    "status": "error",
                    "detail": f"{state}: {error}",
                    "fix": (
                        "--state must be the queue directory created by `slice` "
                        "(it contains queue.sqlite3), not an export .json file"
                    ),
                }
            )

    # Safe repairs: re-run init when the project setup is behind/absent. This
    # rewrites .sonarremedy/ (rules.json kept, version marker, gitignore, the
    # Copilot pointer) idempotently. It never touches a queue or the git state.
    if fix:
        setup_bad = any(
            c["name"] in ("version", "init_files") and c["status"] == "warning" for c in checks
        )
        if setup_bad:
            _write_init_files(project_dir)
            _init_sonarremedy_dir(project_dir)
            for check in checks:
                if check["name"] == "version":
                    check["status"] = "ok"
                    check["detail"] = __version__
            remaining = _missing_init_files(project_dir)
            for check in checks:
                if check["name"] == "init_files":
                    if not remaining:
                        check["status"] = "ok"
                        check["detail"] = "init files present"
                    else:
                        check["detail"] = f"missing: {', '.join(remaining)}"
            checks.append(
                {
                    "name": "fix",
                    "status": "ok",
                    "detail": f"re-ran init; project setup is now at {__version__}",
                }
            )
        hooks_bad = any(c["name"] == "git_hooks" and c["status"] == "warning" for c in checks)
        if hooks_bad:
            try:
                installed = sonar_hooks.install(project_dir)
            except Exception as error:
                installed = {"status": "blocked", "reason": str(error)}
            for check in checks:
                if check["name"] == "git_hooks" and installed.get("status") in ("ok", "installed"):
                    check["status"] = "ok"
                    check["detail"] = "pre-push hook present"
            checks.append(
                {
                    "name": "fix_hooks",
                    "status": "ok" if installed.get("status") in ("ok", "installed") else "info",
                    "detail": f"pre-push hook: {installed.get('status')}",
                }
            )
        if not setup_bad and not hooks_bad:
            checks.append(
                {"name": "fix", "status": "info", "detail": "nothing safe to fix automatically"}
            )

        barrier_bad = any(
            c["name"] == "barrier"
            and c["status"] == "warning"
            and c.get("detail") == "orphaned integration barrier, tree verified unchanged"
            for c in checks
        )
        if barrier_bad and repo:
            import debt_executor

            released = debt_executor.release_barrier(repo, execute=True)
            for check in checks:
                if check["name"] == "barrier" and released.get("status") == "released":
                    check["status"] = "ok"
                    check["detail"] = "orphaned barrier released"
            checks.append(
                {
                    "name": "fix_barrier",
                    "status": "ok" if released.get("status") == "released" else "info",
                    "detail": f"barrier release: {released.get('status')}",
                }
            )

    issues = [c for c in checks if c["status"] == "error"]
    warnings = [c for c in checks if c["status"] == "warning"]
    status = "error" if issues else ("warning" if warnings else "ok")
    return {"status": status, "checks": checks}


def _init_sonarremedy_dir(target: str) -> dict[str, Any]:
    """Create .sonarremedy/ (rules.json + generated subdirs); idempotent."""
    base = _sonarremedy_dir(target)
    created: list[str] = []
    for sub in ("queues", "runs", "temp"):
        directory = os.path.join(base, sub)
        if not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
            created.append(sub)
    rules = os.path.join(base, "rules.json")
    if not os.path.isfile(rules):
        with open(rules, "w", encoding="utf-8") as handle:
            json.dump({"whitelist": [], "blacklist": []}, handle, indent=2, sort_keys=True)
            handle.write("\n")
        created.append("rules.json")
    version = _track_version(target)
    return {
        "base": base,
        "created": created,
        "gitignore_added": _ensure_gitignore(target),
        "version": version,
    }


def _clean(target: str) -> dict[str, Any]:
    """Remove generated state (queues/runs/temp) + sibling worktrees; keep rules.json."""
    removed = []
    for sub in ("queues", "runs", "temp"):
        directory = os.path.join(_sonarremedy_dir(target), sub)
        if os.path.isdir(directory):
            shutil.rmtree(directory, ignore_errors=True)
            removed.append(".sonarremedy/" + sub)
    worktrees = _worktree_root(target)
    if os.path.isdir(worktrees):
        shutil.rmtree(worktrees, ignore_errors=True)
        removed.append(os.path.basename(worktrees))
    return {"status": "cleaned", "removed": removed}


def _reset(target: str) -> dict[str, Any]:
    """Remove the whole .sonarremedy/ + sibling worktrees (back to zero)."""
    removed = []
    base = _sonarremedy_dir(target)
    if os.path.isdir(base):
        shutil.rmtree(base, ignore_errors=True)
        removed.append(".sonarremedy/")
    worktrees = _worktree_root(target)
    if os.path.isdir(worktrees):
        shutil.rmtree(worktrees, ignore_errors=True)
        removed.append(os.path.basename(worktrees))
    return {"status": "reset", "removed": removed}


AUTOPILOT_PHASES = ("setup", "slice", "run", "configure", "integrate", "status")


def detect_checks(repo: str, state: str | None = None) -> dict[str, Any]:
    """Inspect a target checkout and draft its checks.json (read-only).

    Discovery only: every value comes from the repo or the local disk, the
    human completes the 1-2 judgments (policy/reason or RED markers) and
    approves explicitly via `configure --approve-checks-sha256`. Nothing is
    written or executed here.
    """
    import debt_queue

    # Canonicalize first: everything derived (solution, test paths, exe) must
    # use on-disk case, or validate_config blocks it with case_alias later.
    # The tool must never emit a path its own validator rejects.
    root = str(debt_queue.canonical_case(os.path.abspath(repo)))
    detected: dict[str, Any] = {}
    missing: list[str] = []

    def _solutions() -> list[str]:
        found: list[str] = []
        try:
            entries = sorted(os.scandir(root), key=lambda entry: entry.name)
        except OSError:
            return []
        for entry in entries:
            if entry.is_file() and entry.name.lower().endswith((".sln", ".slnx")):
                found.append(entry.name)
        if found:
            return sorted(found)
        for entry in entries:
            if entry.is_dir() and entry.name != ".git":
                try:
                    inner = sorted(os.scandir(entry.path), key=lambda item: item.name)
                except OSError:
                    continue
                for item in inner:
                    if item.is_file() and item.name.lower().endswith((".sln", ".slnx")):
                        found.append(f"{entry.name}/{item.name}")
        return sorted(found)

    def _test_projects() -> list[str]:
        found: list[str] = []
        for base, dirs, names in os.walk(root):
            depth = os.path.relpath(base, root).count(os.sep)
            if depth > 3:
                dirs[:] = []
                continue
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in (".git", "node_modules", "bin", "obj", ".sonarremedy")
            )
            for name in sorted(names):
                lowered = name.lower()
                if lowered.endswith(".csproj") and (
                    "tests." in lowered or lowered.endswith(("tests.csproj", "test.csproj"))
                ):
                    found.append(
                        os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
                    )
        return sorted(found)

    slns = _solutions()
    if len(slns) == 1:
        detected["solution"] = slns[0]
    elif slns:
        missing.append(f"multiple solutions found: {', '.join(slns)} — choose one")
    else:
        missing.append("no .sln/.slnx solution found at the repo root (one level deep)")

    tests = _test_projects()
    if tests:
        detected["test_projects"] = tests
    else:
        missing.append("no *Tests.csproj found — declare test_paths manually")

    exe = shutil.which("dotnet")
    if exe:
        detected["dotnet"] = str(debt_queue.canonical_case(exe))
    else:
        missing.append("dotnet executable not found in PATH — install the .NET SDK")

    package = os.path.join(root, "package.json")
    if os.path.isfile(package):
        try:
            with open(package, encoding="utf-8") as handle:
                scripts = json.load(handle).get("scripts", {})
        except (OSError, ValueError):
            scripts = {}
        if isinstance(scripts, dict):
            reported = {key: scripts[key] for key in ("test", "build") if key in scripts}
            if reported:
                detected["node_scripts"] = reported

    draft: dict[str, Any] | None = None
    if "solution" in detected and "test_projects" in detected and "dotnet" in detected:
        with open(detected["dotnet"], "rb") as handle:
            sha = debt_queue.digest(handle.read(debt_queue.MAX_SOURCE))
        draft = {
            "version": 1,
            "target": root,
            "branch": "[HUMAN: bound queue branch, e.g. main]",
            "policy": "characterization",
            "characterization_reason": "[HUMAN: describe the behavior-preserving refactor]",
            "test_paths": detected["test_projects"],
            "expected_red": {},
            "allowed_outputs": [],
            "checks": [
                {
                    "name": "build",
                    "kind": "build",
                    "argv": [detected["dotnet"], "build", detected["solution"]],
                    "executable_sha256": sha,
                    "cwd": ".",
                    "timeout_seconds": 600,
                },
                {
                    "name": "tests",
                    "kind": "trx",
                    "argv": [
                        detected["dotnet"],
                        "test",
                        detected["test_projects"][0],
                        "--logger",
                        "trx;LogFileName=tests.trx",
                        "--results-directory",
                        "{run}",
                    ],
                    "executable_sha256": sha,
                    "cwd": ".",
                    "timeout_seconds": 600,
                    "report": "tests.trx",
                },
            ],
        }
        missing.extend(
            [
                "complete the [HUMAN] fields (or switch to red-first with expected_red markers from a real failing run)",
                "save the draft as checks.json, then dry-run configure to review checks_sha256",
            ]
        )

    if state:
        follow = (
            f"save the draft as checks.json, complete the [HUMAN] fields, then "
            f"`sonarremedy configure --state {os.path.abspath(state)} --repo {root} --checks <file>`"
        )
    else:
        follow = (
            "save the draft as checks.json, complete the [HUMAN] fields, then "
            "`sonarremedy configure --state <queue> --repo "
            f"{root} --checks <file>`"
        )
    return {
        "status": "detected" if draft is not None else "missing",
        "repo": root,
        "detected": detected,
        "draft": draft,
        "missing": missing,
        "next_command": follow,
    }


def autopilot(
    repo: str,
    state: str,
    export: str | None = None,
    limit: int = 4,
    integrate: bool = False,
    execute: bool = False,
    branch: str = "main",
    proposal_factory: Callable[..., dict[str, Any]] | None = None,
    identity_reader: Callable[[Path], dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Deterministic harness: setup-gate, slice, run, configure-gate, integrate, status.

    Stateless stepper: every call advances as far as the rules allow, then
    reports its phase with the exact next command. The model never chooses a
    step; it only fills bounded proposals the harness asks for. The optional
    factory/reader callbacks are local test seams, not CLI providers.
    """
    import debt_queue
    import debt_runner

    # Canonicalize case from disk (temp dirs and typed paths drift in case;
    # links and missing tails are preserved for downstream checks to block).
    repo_abs = str(debt_queue.canonical_case(repo))
    state_abs = str(debt_queue.canonical_case(state))
    export_abs = str(debt_queue.canonical_case(export)) if export else None
    resume_command = f"sonarremedy autopilot --state {state_abs} --repo {repo_abs} --execute"
    if integrate:
        resume_command += " --integrate"
    if type(limit) is not int or not 1 <= limit <= 8:
        raise debt_queue.Blocked("invalid_autopilot_bounds")
    if not execute:
        return {
            "status": "dry-run",
            "phases": list(AUTOPILOT_PHASES),
            "repo": repo_abs,
            "state": state_abs,
            "integrate": integrate,
            "notice": "No files or processes; use --execute to advance the harness.",
        }
    setup = _check(repo_abs)
    if setup.get("status") != "up_to_date":
        fix = f"sonarremedy init --dir {repo_abs}"
        return {
            "status": "blocked",
            "phase": "setup",
            "reason": setup.get("status"),
            "detail": setup,
            "fix": f"run `{fix}`",
            "next_command": fix,
        }
    sliced: dict[str, Any] | None = None
    if not os.path.isfile(os.path.join(state_abs, "queue.sqlite3")):
        if not export:
            fix = (
                f"sonarremedy autopilot --state {state_abs} "
                f"--repo {repo_abs} --export <export.json> --execute"
            )
            return {
                "status": "blocked",
                "phase": "slice",
                "reason": "queue_missing_export_required",
                "fix": f"run fetch first, then `{fix}`",
                "next_command": fix,
            }
        sliced = debt_queue.slice_queue(
            repo_abs,
            export_abs,
            state_abs,
            branch,
            execute=True,
            identity_reader=identity_reader,
        )
    work = debt_queue.Queue(state_abs, target=repo_abs, identity_reader=identity_reader)
    try:
        ran = debt_runner.run(
            work,
            provider="manual",
            execute=True,
            resume=True,
            integrate=integrate,
            limit=limit,
            proposal_factory=proposal_factory,
        )
    except Exception as error:
        if type(error).__name__ == "Blocked" and str(error) == "reviewed_execution_config_required":
            fix = (
                f"sonarremedy configure --state {state_abs} --repo {repo_abs} "
                "--checks <checks.json> --approve-checks-sha256 <sha256> --execute"
            )
            return {
                "status": "blocked",
                "phase": "configure",
                "reason": "reviewed_execution_config_required",
                "fix": f"bind reviewed checks first: `{fix}`",
                "next_command": fix,
            }
        raise
    if ran.get("status") == "awaiting_proposals":
        return {
            "status": "awaiting_proposals",
            "phase": "run",
            "waiting": ran.get("waiting", []),
            "next_command": f"write one proposal per waiting proposal_path, then `{resume_command}`",
        }
    if ran.get("status") == "proposals_ready":
        jobs = ran.get("jobs", [])
        fix = (
            f"sonarremedy configure --state {state_abs} --repo {repo_abs} "
            "--checks <checks.json> --approve-checks-sha256 <sha256> --execute"
        )
        return {
            "status": "proposals_ready",
            "phase": "configure",
            "jobs": jobs,
            "notice": "Integrate only after explicit check configuration/review.",
            "next_command": f"`{fix}`, then `{resume_command} --integrate`",
        }
    monitor = work.monitor()
    action = next_action(monitor["entry_states"])
    finished: dict[str, Any] = {
        "status": ran.get("status"),
        "phase": "status",
        "runner": ran,
        "next_action": action,
        **estimate(monitor["entry_states"]),
    }
    if sliced is not None:
        finished["sliced"] = {"entries": sliced.get("entries"), "jobs": sliced.get("jobs")}
    if action == "re_scan_required":
        finished["next_step"] = (
            "commit+push fixes, re-run the Sonar pipeline, git pull, then re-run fetch+slice"
        )
        finished["next_command"] = finished["next_step"]
    elif action in ("run_batch", "resume"):
        finished["next_command"] = resume_command
    elif action == "integrate":
        finished["next_command"] = resume_command if integrate else resume_command + " --integrate"
    else:
        finished["next_command"] = "done; no commits/push in this runner"
    return finished


def recover(
    rcfg: dict[str, Any],
    repo: str,
    state_base: str,
    checks_path: str,
    approved_sha: str,
    branch: str = "main",
    limit: int = 4,
    execute: bool = False,
    proposal_factory: Callable[..., dict[str, Any]] | None = None,
    identity_reader: Callable[[Path], dict[str, str]] | None = None,
    process_runner: Callable[..., dict[str, Any]] | None = None,
    control_root: str | Path | None = None,
    max_cycles: int = 10,
) -> dict[str, Any]:
    """Loop the recovery cycle until done or impossible.

    Each cycle: fetch → slice → run (manual proposals) → configure → integrate →
    status. On `re_scan_required` it re-analyzes (publish to Sonar) and starts the
    next cycle. Stops on 0 issues, no progress (everything terminal), or the model
    asking for proposals (`awaiting_proposals`). The model never chooses a step.
    """
    import debt_executor
    import debt_queue
    import debt_runner

    if type(limit) is not int or not 1 <= limit <= 8:
        raise debt_queue.Blocked("invalid_recover_bounds")
    if type(max_cycles) is not int or not 1 <= max_cycles <= 100:
        raise debt_queue.Blocked("invalid_recover_cycles")
    repo_abs = str(debt_queue.canonical_case(repo))
    state_abs = str(debt_queue.canonical_case(state_base))
    checks_abs = str(debt_queue.canonical_case(checks_path))
    if not execute:
        return {
            "status": "dry-run",
            "repo": repo_abs,
            "state_base": state_abs,
            "max_cycles": max_cycles,
            "notice": "No files or processes; use --execute to run the loop.",
        }
    if not approved_sha:
        raise debt_queue.Blocked("approved_checks_sha_required")
    checks = debt_queue.parse_json(debt_queue.read_bytes(checks_abs, debt_queue.MAX_EXPORT))
    token_env = rcfg["sonar"]["token_env"]
    if not os.environ.get(token_env):
        raise debt_queue.Blocked(f"{token_env} must be present in the environment")
    # Materialize the base directory: slice_queue requires its parent to exist.
    os.makedirs(state_abs, exist_ok=True)
    project = rcfg["sonar"]["project_key"]
    summary = {
        "status": "done",
        "cycles": 0,
        "applied": 0,
        "locally_verified": 0,
        "deferred": 0,
        "failed": 0,
    }
    for cycle in range(max_cycles):
        output = _default_output(project)
        fetched = sonar_fetch.fetch(build_fetch_config(rcfg, repo_abs), output)
        if fetched.get("issues_total", 0) == 0:
            summary["status"] = "done"
            summary["cycles"] = cycle
            break
        export = fetched["export"]
        state = os.path.join(state_abs, f"cycle-{cycle}")
        debt_queue.slice_queue(
            repo_abs, export, state, branch, execute=True, identity_reader=identity_reader
        )
        work = debt_queue.Queue(state, target=repo_abs, identity_reader=identity_reader)
        ran = debt_runner.run(
            work,
            provider="manual",
            execute=True,
            resume=True,
            limit=limit,
            proposal_factory=proposal_factory,
        )
        if ran.get("status") == "awaiting_proposals":
            return {
                "status": "awaiting_proposals",
                "state": state,
                "cycles": cycle,
                "waiting": ran.get("waiting", []),
                "next_command": f"write one proposal per waiting proposal_path, then `sonarremedy recover --state {state_abs} --repo {repo_abs} --checks {checks_abs} --approve-checks-sha256 {approved_sha} --execute`",
            }
        if ran.get("status") == "quiescent":
            summary["cycles"] = cycle
            summary["status"] = "impossible"
            break
        debt_executor.configure(work, checks, approved_sha256=approved_sha, execute=True)
        monitor = work.monitor()
        applied = monitor["states"].get("locally_verified", 0)
        for state_row in ("applied", "locally_verified"):
            summary[state_row] = summary.get(state_row, 0) + monitor["states"].get(state_row, 0)
        summary["deferred"] += monitor["states"].get("deferred", 0)
        summary["failed"] += monitor["states"].get("failed", 0)
        summary["cycles"] = cycle + 1
        if applied == 0 and monitor["states"].get("proposed", 0) == 0:
            summary["status"] = "impossible"
            break
        if next_action(monitor["entry_states"]) == "re_scan_required":
            if cycle + 1 >= max_cycles:
                summary["status"] = "cycle_budget_exhausted"
                break
            _analyze_publish(rcfg, repo_abs, branch)
            continue
        break
    return summary


def _analyze_publish(rcfg: dict[str, Any], repo: str, branch: str) -> None:
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sonar_compact.ps1")
    argv = _analyze_argv(rcfg, script, repo, branch, skip_pull=True)
    completed = subprocess.run(argv)
    if completed.returncode != 0:
        raise rc.ConfigError(f"analyze pipeline failed with exit {completed.returncode}")


def next_action(states: dict[str, int]) -> str:
    """Deterministic next step from a queue's entry_states."""
    if states.get("pending", 0) > 0:
        return "run_batch"
    if states.get("leased", 0) > 0:
        return "resume"
    if states.get("proposed", 0) > 0:
        return "integrate"
    resolved = (
        states.get("applied", 0)
        + states.get("locally_verified", 0)
        + states.get("deferred", 0)
        + states.get("failed", 0)
    )
    if resolved > 0:
        return "re_scan_required"
    return "done"


def _analyze_argv(
    rcfg: dict[str, Any], script: str, repo: str, branch: str, skip_pull: bool = False
) -> list[str]:
    argv = [
        "pwsh",
        "-NoProfile",
        "-File",
        script,
        "-WorktreePath",
        repo,
        "-BranchName",
        branch,
        "-ProjectKey",
        rcfg["sonar"]["project_key"],
        "-SonarUrl",
        rcfg["sonar"]["url"],
    ]
    if skip_pull:
        argv.append("-SkipPull")
    return argv


DEFAULT_MINUTES_PER_JOB = 5


def estimate(
    entry_states: dict[str, int], minutes_per_job: float = DEFAULT_MINUTES_PER_JOB
) -> dict[str, int | float]:
    remaining = (
        entry_states.get("pending", 0)
        + entry_states.get("leased", 0)
        + entry_states.get("proposed", 0)
    )
    total = sum(entry_states.values())
    estimated_minutes = remaining * minutes_per_job
    return {
        "remaining_jobs": remaining,
        "resolved_jobs": total - remaining,
        "total_jobs": total,
        "estimated_minutes": estimated_minutes,
        "estimated_hours": round(estimated_minutes / 60.0, 2),
    }


REFACTOR_KINDS = ("smells", "security", "duplication")


def schedule_plan(jobs: list[dict[str, Any]]) -> dict[str, int]:
    """Group pending jobs: parallel-safe (single-job files) vs serial (shared files),
    and flag refactor-heavy kinds. Files with disjoint write paths can parallelize."""
    files = {}
    for job in jobs:
        files.setdefault(job["path"], []).append(job["kind"])
    multi = [p for p, kinds in files.items() if len(kinds) > 1]
    refactor = [p for p, kinds in files.items() if any(k in REFACTOR_KINDS for k in kinds)]
    parallel = len(files) - len(multi)
    return {
        "jobs": len(jobs),
        "files": len(files),
        "parallel_files": parallel,
        "serial_files": len(multi),
        "refactor_files": len(refactor),
        "recommended_workers": min(8, parallel),
    }


FIX_HINTS = {
    "security": (
        "Fix the vulnerability without weakening protections. Validate/escape untrusted input; "
        "avoid unsafe deserialization, SQL injection, path traversal and hardcoded secrets; follow "
        "least privilege. Never add suppressions. If a safe minimal fix is unclear, defer."
    ),
    "hotspots": (
        "Security hotspot requiring human review. Do not auto-fix blindly. Propose a minimal "
        "clarifying change only if clearly safe; otherwise defer for human disposition."
    ),
    "smells": (
        "Fix the code smell at the flagged line with the minimal idiomatic change (simplify "
        "complexity, remove duplication, use clearer names/structure). Preserve observable behavior. "
        "Avoid large refactors; if the smell spans many parts, defer."
    ),
    "coverage": (
        "Coverage jobs come from explicit file/line evidence. Add or adjust a focused test that "
        "exercises the uncovered line/branch. Never weaken or remove assertions; no suppressions."
    ),
    "duplication": (
        "Extract the duplicated code into a shared function or constant and reuse it, preserving "
        "behavior. If extraction is unsafe or spans files with divergent logic, defer."
    ),
}


def hint_for(kind: str) -> str:
    return FIX_HINTS.get(kind, "")


EXTENSION_LANGUAGE = {
    ".py": "python",
    ".cs": "csharp",
    ".vb": "vb",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".cshtml": "csharp",
    ".razor": "csharp",
}

# Distilled per-language fix guidance (kept short). The full skill lives in
# skills/<language>-index.md; the orchestrator reads it and injects this summary
# alongside hint_for(kind) so the worker fixes using BOTH the kind and the language.
LANGUAGE_HINTS = {
    "python": "Python: stdlib-first; ruff/mypy-clean code, f-strings, pathlib, type hints; no bare noqa/type:ignore.",
    "csharp": "C#/.NET: see dotnet/skills; prefer nullability, LINQ, analyzers; avoid #pragma warning disable.",
    "vb": "VB.NET: follow .NET conventions; avoid SuppressMessage unless justified.",
    "java": "Java: prefer records, streams, sealed types; avoid @SuppressWarnings unless justified.",
    "kotlin": "Kotlin: prefer data classes and when; avoid @Suppress unless justified.",
    "typescript": "TypeScript/Angular: strict mode, type guards, no @ts-ignore; Angular: OnPush, trackBy, reactive forms.",
    "javascript": "JavaScript/React: const/let, arrow functions, no eslint-disable; React: hooks rules, keyed lists.",
    "go": "Go: gofmt-clean, explicit error handling, no unused imports.",
    "rust": "Rust: prefer match + Option/Result; no #[allow] unless justified.",
    "cpp": "C/C++: prefer RAII and smart pointers; avoid #pragma suppression.",
}


def language_for(path: str) -> str:
    """Map a file path to its language key ('' when unknown)."""
    return EXTENSION_LANGUAGE.get(os.path.splitext(path)[1].lower(), "")


def language_hint_for(path: str) -> str:
    """Short language-specific fix guidance for a job's file path."""
    return LANGUAGE_HINTS.get(language_for(path), "")


def _configure_projects_interactive() -> int:
    """Interactively register multiple projects; each needs name, Sonar URL, key, token env."""
    saved: list[str] = []
    while True:
        try:
            name = input("Project name (empty to finish): ").strip()
        except EOFError:
            break
        if not name:
            break
        url = input("  Sonar URL: ").strip()
        key = input("  Project key: ").strip()
        token_env = input("  Token env var [SONAR_TOKEN]: ").strip() or "SONAR_TOKEN"
        repo_url = input("  Repo URL: ").strip()
        worktree = input("  Worktree root: ").strip()
        branch = input("  Main branch [main]: ").strip() or "main"
        cfg = {
            "version": 1,
            "sonar": {"url": url, "project_key": key, "token_env": token_env},
            "repository": {
                "url": repo_url,
                "pat_env": "GIT_PAT",
                "main_branch": branch,
                "propagation_branches": [],
            },
            "worktrees": {"root": worktree},
            "provider": "manual",
        }
        errors = rc.validate(cfg)
        if errors:
            for error in errors:
                print(f"  ! {error}")
            print("  Skipping this project; fix the fields and retry.")
            continue
        rc.save_project(name, cfg)
        saved.append(name)
        print(f"  saved: {name}")
    print(json.dumps({"status": "ok", "saved": saved}, sort_keys=True))
    return 0


def _update(path: str | None) -> int:
    """git pull, then reinstall in a detached process (so pip can replace the .exe)."""
    # Find the clone from anywhere: --path wins, then this module's own location
    # (works for editable installs / source), then the current directory.
    if path:
        target = os.path.abspath(path)
    else:
        module_dir = os.path.dirname(os.path.abspath(__file__))
        target = module_dir if os.path.isdir(os.path.join(module_dir, ".git")) else os.getcwd()
    if not os.path.isdir(os.path.join(target, ".git")):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": (
                        f"not a git checkout: {target}. Run from your SonarRemedy clone, "
                        "or pass --path <clone>."
                    ),
                },
                sort_keys=True,
            )
        )
        return 2
    try:
        subprocess.run(["git", "-C", target, "pull"], check=True)
    except subprocess.CalledProcessError as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, sort_keys=True))
        return 2
    # Reinstall in a DETACHED process that waits for THIS process to exit, so pip
    # can replace the locked console-script .exe on Windows.
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    reinstall = (
        "import subprocess, sys, time; "
        f"time.sleep(2); "
        f"subprocess.run([sys.executable, '-m', 'pip', 'install', '-e', {target!r}])"
    )
    subprocess.Popen(
        [sys.executable, "-c", reinstall],
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    print(
        json.dumps(
            {"status": "updated", "path": target, "note": "reinstall running in the background"},
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="explicit config file (overrides --project)")
    parser.add_argument("--project", help="project name in ~/.sonar-remedy/projects/")
    parser.add_argument("--version", action="version", version=f"sonarremedy {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="collect Sonar issues using the persisted config")
    fetch.add_argument("--repo", help="local main checkout (overrides repository.local_path)")
    fetch.add_argument("--output", help="output directory (outside the repo)")
    fetch.add_argument(
        "--kinds",
        help="comma-separated finding categories to pull: "
        + ",".join(sonar_fetch.FETCH_KINDS)
        + " (default: all)",
    )
    slice_cmd = commands.add_parser(
        "slice", help="create the durable queue from an export using the persisted config"
    )
    slice_cmd.add_argument("--repo", help="local main checkout (overrides repository.local_path)")
    slice_cmd.add_argument("--export", help="export.json produced by fetch")
    slice_cmd.add_argument("--state", help="new queue directory outside the target")
    slice_cmd.add_argument("--execute", action="store_true")
    run_cmd = commands.add_parser("run", help="lease/process a bounded manual proposal batch")
    run_cmd.add_argument("--state", required=True, help="the queue directory created by slice")
    run_cmd.add_argument("--repo", help="assert the queue target (overrides repository.local_path)")
    run_cmd.add_argument("--limit", type=int, default=4)
    run_cmd.add_argument("--resume", action="store_true")
    run_cmd.add_argument("--integrate", action="store_true")
    run_cmd.add_argument("--execute", action="store_true")
    configure_cmd = commands.add_parser(
        "configure", help="bind reviewed check commands and the current target snapshot"
    )
    configure_cmd.add_argument("--state", required=True)
    configure_cmd.add_argument("--checks", required=True, help="checks JSON file to review/bind")
    configure_cmd.add_argument("--approve-checks-sha256")
    configure_cmd.add_argument("--repo", help="assert the queue target")
    configure_cmd.add_argument("--execute", action="store_true")
    integrate_cmd = commands.add_parser(
        "integrate", help="serially apply a recorded proposal and run bound checks"
    )
    integrate_cmd.add_argument("--state", required=True)
    integrate_cmd.add_argument("--job", required=True)
    integrate_cmd.add_argument("--repo", help="assert the queue target")
    integrate_cmd.add_argument("--execute", action="store_true")
    commands.add_parser("projects", help="list saved project configs")
    status_cmd = commands.add_parser("status", help="read-only: report the queue's next action")
    status_cmd.add_argument("--state", required=True)
    status_cmd.add_argument("--minutes-per-job", type=float, default=DEFAULT_MINUTES_PER_JOB)
    progress_cmd = commands.add_parser("progress", help="write a human-readable progress file")
    progress_cmd.add_argument("--state", required=True)
    progress_cmd.add_argument("--minutes-per-job", type=float, default=DEFAULT_MINUTES_PER_JOB)
    schedule_cmd = commands.add_parser(
        "schedule", help="read-only: show the parallel/serial plan for pending jobs"
    )
    schedule_cmd.add_argument("--state", required=True)
    analyze_cmd = commands.add_parser(
        "analyze", help="run the local pipeline to generate+publish Sonar results"
    )
    analyze_cmd.add_argument(
        "--script", help="local pipeline .ps1 (default: the pack's sonar_compact.ps1)"
    )
    analyze_cmd.add_argument("--repo", help="local checkout (overrides repository.local_path)")
    analyze_cmd.add_argument("--branch", help="branch to analyze (default: config main_branch)")
    analyze_cmd.add_argument("--skip-pull", action="store_true")
    analyze_cmd.add_argument("--execute", action="store_true")
    runall_cmd = commands.add_parser("run-all", help="fetch + slice every chunk into its own queue")
    runall_cmd.add_argument("--repo", help="local checkout (overrides repository.local_path)")
    runall_cmd.add_argument("--state", required=True, help="base directory for chunk queues")
    runall_cmd.add_argument("--output", help="fetch output directory")
    runall_cmd.add_argument("--execute", action="store_true", help="create the chunk queues")
    autopilot_cmd = commands.add_parser(
        "autopilot",
        help="deterministic harness: setup-gate, slice, run, configure-gate, integrate, status",
    )
    autopilot_cmd.add_argument(
        "--state", required=True, help="the queue directory (created by slice)"
    )
    autopilot_cmd.add_argument("--repo", help="local checkout (overrides repository.local_path)")
    autopilot_cmd.add_argument(
        "--export", help="export.json produced by fetch (to slice a new queue)"
    )
    autopilot_cmd.add_argument("--limit", type=int, default=4)
    autopilot_cmd.add_argument("--integrate", action="store_true")
    autopilot_cmd.add_argument("--execute", action="store_true")
    recover_cmd = commands.add_parser(
        "recover", help="loop the debt-recovery cycle until done or impossible"
    )
    recover_cmd.add_argument("--state", required=True, help="base directory for cycle queues")
    recover_cmd.add_argument("--repo", help="local checkout (overrides repository.local_path)")
    recover_cmd.add_argument("--branch", help="branch to recover (default: config main_branch)")
    recover_cmd.add_argument("--checks", required=True, help="checks.json file to bind")
    recover_cmd.add_argument(
        "--approve-checks-sha256", required=True, help="reviewed checks sha256"
    )
    recover_cmd.add_argument("--limit", type=int, default=4)
    recover_cmd.add_argument("--max-cycles", type=int, default=10)
    recover_cmd.add_argument("--execute", action="store_true")
    detect_cmd = commands.add_parser(
        "detect-checks", help="inspect a checkout and draft its checks.json (read-only)"
    )
    detect_cmd.add_argument("--repo", help="local checkout (default: current directory)")
    detect_cmd.add_argument("--state", help="queue directory (echoed in the next command)")
    cfgproj_cmd = commands.add_parser(
        "configure-project", help="save a project config non-interactively"
    )
    cfgproj_cmd.add_argument("--name", required=True)
    cfgproj_cmd.add_argument("--sonar-url", required=True)
    cfgproj_cmd.add_argument("--project-key", required=True)
    cfgproj_cmd.add_argument("--repo-url", required=True)
    cfgproj_cmd.add_argument("--local-path", required=True)
    cfgproj_cmd.add_argument("--worktree-root", required=True)
    cfgproj_cmd.add_argument("--main-branch", default=None)
    cfgproj_cmd.add_argument("--provider", default="manual")
    cfgproj_cmd.add_argument("--allow-http", action="store_true")
    cfgproj_cmd.add_argument(
        "--token-env", default="SONAR_TOKEN", help="env var name for the Sonar token"
    )
    cfgproj_cmd.add_argument("--pat-env", default="GIT_PAT", help="env var name for the Git PAT")
    commands.add_parser("configure-projects", help="interactively register multiple projects")
    update_cmd = commands.add_parser(
        "update", help="git pull + reinstall the pack (works from anywhere)"
    )
    update_cmd.add_argument("--path", help="SonarRemedy clone directory (auto-detected if omitted)")
    init_cmd = commands.add_parser(
        "init", help="write .vscode/mcp.json + the Copilot instruction into a project"
    )
    init_cmd.add_argument("--dir", help="target project directory (default: current)")
    clean_cmd = commands.add_parser(
        "clean", help="remove generated state + sibling worktrees (keep rules.json)"
    )
    clean_cmd.add_argument("--dir", help="target project directory (default: current)")
    reset_cmd = commands.add_parser(
        "reset", help="remove .sonarremedy/ entirely + sibling worktrees (back to zero)"
    )
    reset_cmd.add_argument("--dir", help="target project directory (default: current)")
    check_cmd = commands.add_parser(
        "check", help="report whether the project's SonarRemedy setup is up to date"
    )
    check_cmd.add_argument("--dir", help="target project directory (default: current)")
    doctor_cmd = commands.add_parser(
        "doctor", help="diagnose the setup and a queue's identity (with fixes)"
    )
    doctor_cmd.add_argument("--dir", help="target project directory (default: current)")
    doctor_cmd.add_argument("--state", help="queue directory to diagnose")
    doctor_cmd.add_argument("--repo", help="local checkout to compare against the queue binding")
    doctor_cmd.add_argument(
        "--fix",
        action="store_true",
        help="apply the safe repairs (re-run init if outdated or init files are missing)",
    )
    rules_cmd = commands.add_parser("rules", help="manage exclusion rules (whitelist/blacklist)")
    rules_cmd.add_argument("--dir", help="target project directory (default: current)")
    rules_cmd.add_argument(
        "action",
        choices=["list", "allow", "block", "remove"],
        help="list | allow (whitelist) | block (blacklist) | remove",
    )
    rules_cmd.add_argument("rule", nargs="?", help="rule name (for allow/block/remove)")
    supp_cmd = commands.add_parser(
        "scan-suppressions", help="detect code-level suppressions that may evade Sonar"
    )
    supp_cmd.add_argument("--repo", help="local checkout (overrides repository.local_path)")
    exclusions_cmd = commands.add_parser(
        "scan-exclusions", help="detect Sonar exclusions/suppressions by language + category"
    )
    exclusions_cmd.add_argument("--repo", help="local checkout (overrides repository.local_path)")
    report_cmd = commands.add_parser(
        "report", help="list applied fixes and the human follow-up each requires"
    )
    report_cmd.add_argument("--state", required=True, help="the queue directory created by slice")
    args = parser.parse_args(argv)
    try:
        if args.command == "projects":
            print(json.dumps({"status": "ok", "projects": rc.list_projects()}, sort_keys=True))
            return 0
        if args.command == "status":
            import debt_queue

            work = debt_queue.Queue(args.state)
            monitor = work.monitor()
            action = next_action(monitor["entry_states"])
            result = {
                "status": "ok",
                "next_action": action,
                "entry_states": monitor["entry_states"],
                **estimate(monitor["entry_states"], args.minutes_per_job),
            }
            if action == "re_scan_required":
                result["next_step"] = (
                    "commit+push fixes, re-run the Sonar pipeline, git pull, then re-run fetch+slice"
                )
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "progress":
            import debt_queue

            work = debt_queue.Queue(args.state)
            monitor = work.monitor()
            action = next_action(monitor["entry_states"])
            est = estimate(monitor["entry_states"], args.minutes_per_job)
            pct = (100.0 * est["resolved_jobs"] / est["total_jobs"]) if est["total_jobs"] else 0.0
            lines = ["# SonarRemedy progress", ""]
            lines.append(f"done={est['resolved_jobs']}/{est['total_jobs']} ({pct:.1f}%)")
            lines.append(f"remaining={est['remaining_jobs']}")
            lines.append(
                "estimated=~{:.2f} hours ({:.1f} min/job)".format(
                    est["estimated_hours"], args.minutes_per_job
                )
            )
            lines.append(f"next_action={action}")
            path = os.path.join(args.state, "progress.md")
            os.makedirs(args.state, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            print(
                json.dumps(
                    {"status": "ok", "path": os.path.abspath(path), "next_action": action, **est},
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "schedule":
            import debt_queue

            work = debt_queue.Queue(args.state)
            plan = schedule_plan(work.pending())
            print(json.dumps({"status": "ok", **plan}, sort_keys=True))
            return 0
        if args.command == "report":
            import debt_queue

            work = debt_queue.Queue(args.state)
            print(json.dumps(work.report(), sort_keys=True))
            return 0
        if args.command == "configure-project":
            branch = args.main_branch or rc.detect_branch(args.sonar_url) or "main"
            cfg = {
                "version": 1,
                "sonar": {
                    "url": args.sonar_url,
                    "project_key": args.project_key,
                    "token_env": args.token_env,
                },
                "repository": {
                    "url": args.repo_url,
                    "pat_env": args.pat_env,
                    "local_path": args.local_path,
                    "main_branch": branch,
                    "propagation_branches": [],
                },
                "worktrees": {"root": args.worktree_root},
                "provider": args.provider,
            }
            if args.allow_http:
                cfg["sonar"]["allow_http"] = True
            rc.save_project(args.name, cfg)
            print(
                json.dumps(
                    {
                        "status": "configured",
                        "project": args.name,
                        "config": rc.project_path(args.name),
                        "main_branch": branch,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "configure-projects":
            return _configure_projects_interactive()
        if args.command == "update":
            return _update(args.path)
        if args.command == "init":
            target = os.path.abspath(args.dir or os.getcwd())
            mcp_path, pointer_path, full_path = _write_init_files(target)
            state = _init_sonarremedy_dir(target)
            try:
                hooks = sonar_hooks.install(target)
            except Exception as error:
                hooks = {"status": "blocked", "reason": str(error)}
            print(
                json.dumps(
                    {
                        "status": "initialized",
                        "dir": target,
                        "mcp": mcp_path,
                        "instructions": pointer_path,
                        "full_instructions": full_path,
                        "sonarremedy": state["base"],
                        "created": state["created"],
                        "gitignore_added": state["gitignore_added"],
                        "version": state["version"],
                        "hooks": hooks,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "clean":
            target = os.path.abspath(args.dir or os.getcwd())
            print(json.dumps(_clean(target), sort_keys=True))
            return 0
        if args.command == "reset":
            target = os.path.abspath(args.dir or os.getcwd())
            print(json.dumps(_reset(target), sort_keys=True))
            return 0
        if args.command == "check":
            target = os.path.abspath(args.dir or os.getcwd())
            print(json.dumps(_check(target), sort_keys=True))
            return 0
        if args.command == "doctor":
            target = os.path.abspath(args.dir or os.getcwd())
            print(json.dumps(_doctor(args.repo, args.state, target, fix=args.fix), sort_keys=True))
            return 0
        if args.command == "rules":
            import sonar_exclusions_report as report

            target = os.path.abspath(args.dir or os.getcwd())
            if args.action == "list":
                print(json.dumps(report.list_rules(target), sort_keys=True))
                return 0
            if args.action in ("allow", "block"):
                if not args.rule:
                    raise rc.ConfigError("rules allow/block require a rule name")
                list_name = "whitelist" if args.action == "allow" else "blacklist"
                print(json.dumps(report.add_rule(target, args.rule, list_name), sort_keys=True))
                return 0
            if args.action == "remove":
                if not args.rule:
                    raise rc.ConfigError("rules remove requires a rule name")
                print(json.dumps(report.remove_rule(target, args.rule), sort_keys=True))
                return 0
            raise rc.ConfigError("unknown rules action: " + args.action)
        if args.command == "detect-checks":
            print(json.dumps(detect_checks(args.repo or os.getcwd(), args.state), sort_keys=True))
            return 0
        rcfg = _load_config(args)
        if args.command == "scan-suppressions":
            import sonar_suppressions

            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            if not repo:
                raise rc.ConfigError("--repo is required, or set repository.local_path in config")
            result = sonar_suppressions.scan(repo)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "scan-exclusions":
            import sonar_exclusions_report

            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            if not repo:
                raise rc.ConfigError("--repo is required, or set repository.local_path in config")
            result = sonar_exclusions_report.scan(repo)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "fetch":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            if not repo:
                raise rc.ConfigError("--repo is required, or set repository.local_path in config")
            token_env = rcfg["sonar"]["token_env"]
            if not os.environ.get(token_env):
                raise rc.ConfigError(f"{token_env} must be present in the environment")
            output = args.output or _default_output(args.project)
            kinds = args.kinds.split(",") if args.kinds else None
            result = sonar_fetch.fetch(build_fetch_config(rcfg, repo, kinds), output)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result.get("status") == "collected" else 2
        if args.command == "slice":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            if not repo:
                raise rc.ConfigError("--repo is required, or set repository.local_path in config")
            if not args.export:
                raise rc.ConfigError("--export is required (run fetch first)")
            if not args.state:
                raise rc.ConfigError("--state is required (a new queue directory)")
            import debt_queue

            result = debt_queue.slice_queue(
                repo,
                args.export,
                args.state,
                rcfg["repository"]["main_branch"],
                execute=args.execute,
            )
            if result.get("status") == "created":
                name = _resolve_project_name(args)
                if name:
                    rc.register_queue(name, args.state)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "run":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            import debt_queue
            import debt_runner

            # Assert the repo path only. The queue's binding is the source of truth
            # for the branch/revision (check_identity validates the ACTUAL git state),
            # so we do NOT re-assert the config's main_branch here — a stale config
            # must not block a queue whose bound branch is correct.
            work = debt_queue.Queue(args.state, target=repo)
            result = debt_runner.run(
                work,
                provider="manual",
                execute=args.execute,
                resume=args.resume,
                integrate=args.integrate,
                limit=args.limit,
            )
            print(json.dumps(result, sort_keys=True))
            return (
                2
                if result.get("status") in ("unavailable", "quarantined", "reconciliation_required")
                else 0
            )
        if args.command == "configure":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            import debt_executor
            import debt_queue

            # Assert the repo path only. The queue's binding is the source of truth
            # for the branch/revision (check_identity validates the ACTUAL git state),
            # so we do NOT re-assert the config's main_branch here — a stale config
            # must not block a queue whose bound branch is correct.
            work = debt_queue.Queue(args.state, target=repo)
            checks = debt_queue.parse_json(
                debt_queue.read_bytes(args.checks, debt_queue.MAX_EXPORT)
            )
            result = debt_executor.configure(
                work, checks, approved_sha256=args.approve_checks_sha256, execute=args.execute
            )
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "integrate":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            import debt_executor
            import debt_queue

            # Assert the repo path only. The queue's binding is the source of truth
            # for the branch/revision (check_identity validates the ACTUAL git state),
            # so we do NOT re-assert the config's main_branch here — a stale config
            # must not block a queue whose bound branch is correct.
            work = debt_queue.Queue(args.state, target=repo)
            result = debt_executor.integrate(work, args.job, execute=args.execute)
            print(json.dumps(result, sort_keys=True))
            return (
                2
                if result.get("status") in ("unavailable", "quarantined", "reconciliation_required")
                else 0
            )
        if args.command == "analyze":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            if not repo:
                raise rc.ConfigError("--repo is required, or set repository.local_path in config")
            token_env = rcfg["sonar"]["token_env"]
            if not os.environ.get(token_env):
                raise rc.ConfigError(f"{token_env} must be present in the environment")
            branch = args.branch or rcfg["repository"]["main_branch"]
            script = args.script or os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "sonar_compact.ps1"
            )
            argv = _analyze_argv(rcfg, script, repo, branch, skip_pull=args.skip_pull)
            if not args.execute:
                print(json.dumps({"status": "dry-run", "argv": argv}, sort_keys=True))
                return 0
            import subprocess

            completed = subprocess.run(argv)
            print(
                json.dumps(
                    {
                        "status": "completed" if completed.returncode == 0 else "failed",
                        "returncode": completed.returncode,
                    },
                    sort_keys=True,
                )
            )
            return 0 if completed.returncode == 0 else 2
        if args.command == "run-all":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            if not repo:
                raise rc.ConfigError("--repo is required, or set repository.local_path in config")
            token_env = rcfg["sonar"]["token_env"]
            if not os.environ.get(token_env):
                raise rc.ConfigError(f"{token_env} must be present in the environment")
            import debt_queue

            output = args.output or _default_output(args.project)
            fetch_result = sonar_fetch.fetch(build_fetch_config(rcfg, repo), output)
            exports = fetch_result.get("exports") or [fetch_result["export"]]
            queues = []
            for idx, export in enumerate(exports):
                state = os.path.join(args.state, f"chunk-{idx}")
                if args.execute:
                    debt_queue.slice_queue(
                        repo, export, state, rcfg["repository"]["main_branch"], execute=True
                    )
                queues.append(state)
            print(
                json.dumps(
                    {
                        "status": "prepared" if args.execute else "dry-run",
                        "chunks": len(exports),
                        "exports": exports,
                        "queues": queues,
                        "issues_total": fetch_result.get("issues_total"),
                        "gate": fetch_result.get("gate"),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "autopilot":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            if not repo:
                raise rc.ConfigError("--repo is required, or set repository.local_path in config")
            result = autopilot(
                repo,
                args.state,
                export=args.export,
                limit=args.limit,
                integrate=args.integrate,
                execute=args.execute,
                branch=(rcfg.get("repository") or {}).get("main_branch") or "main",
            )
            print(json.dumps(result, sort_keys=True))
            return (
                2
                if result.get("status") in ("blocked", "quarantined", "reconciliation_required")
                else 0
            )
        if args.command == "recover":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            if not repo:
                raise rc.ConfigError("--repo is required, or set repository.local_path in config")
            result = recover(
                rcfg,
                repo,
                args.state,
                args.checks,
                args.approve_checks_sha256,
                branch=args.branch or (rcfg.get("repository") or {}).get("main_branch") or "main",
                limit=args.limit,
                execute=args.execute,
                max_cycles=args.max_cycles,
            )
            print(json.dumps(result, sort_keys=True))
            return 0
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled", "reason": "operator_cancelled"}))
        return 130
    except Exception as error:
        secret = os.environ.get("SONAR_TOKEN", "")
        # Every module defines its own Blocked(ValueError); recognize them all by
        # name so a debt_queue.Blocked / sonar_suppressions.Blocked / etc. reports
        # its real reason, not the generic "Blocked".
        if isinstance(error, rc.ConfigError) or type(error).__name__ == "Blocked":
            reason = sonar_fetch.redactor(secret)(str(error))
        else:
            # Raw OS/runtime failures must name the file/operation: a bare
            # type ("OSError", "PermissionError") cannot be diagnosed.
            reason = f"{type(error).__name__}: {sonar_fetch.redactor(secret)(str(error))}"
        print(json.dumps({"status": "blocked", "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
