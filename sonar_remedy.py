"""SonarRemedy facade: drive the Sonar debt pipeline from persisted config."""

import argparse
import json
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from typing import Any

import sonar_fetch
import sonar_remedy_config as rc

try:
    __version__ = _dist_version("sonarremedy")
except PackageNotFoundError:  # running from source without an install
    __version__ = "dev"


def build_fetch_config(rcfg: dict[str, Any], repo: str) -> sonar_fetch.Config:
    """Map a persisted SonarRemedy config to sonar_fetch's API Config."""
    url = rcfg["sonar"]["url"]
    return sonar_fetch.Config(
        {
            "adapter": "api",
            "repo": repo,
            "url": url,
            "trusted_url": url,
            "project": rcfg["sonar"]["project_key"],
            "branch": rcfg["repository"]["main_branch"],
            "allow_http": rcfg["sonar"].get("allow_http", False),
        }
    )


def _default_output(project: str | None = None) -> str:
    return os.path.join(os.path.expanduser("~"), ".sonar-remedy", "runs", project or "default")


def _load_config(args: argparse.Namespace) -> dict[str, Any]:
    if args.config:
        return rc.load(args.config)
    if args.project:
        return rc.load_project(args.project)
    return rc.load(rc.default_config_path())


def _write_init_files(target: str) -> tuple[str, str]:
    """Write .vscode/mcp.json + the Copilot instruction into a project."""
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

    github_dir = os.path.join(target, ".github")
    os.makedirs(github_dir, exist_ok=True)
    instructions_path = os.path.join(github_dir, "copilot-instructions.md")
    source = os.path.join(pack, "host-agents", "copilot-instructions.md")
    if os.path.isfile(source):
        with open(source, encoding="utf-8") as handle:
            content = handle.read()
    else:
        content = (
            "# Technical debt → SonarRemedy\n\n"
            "Use the `sonar_remedy_*` MCP tools to recover Sonar debt.\n"
        )
    with open(instructions_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return mcp_path, instructions_path


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
        "-ProjectBaseDir",
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
    """git pull + pip install the pack from its clone."""
    target = os.path.abspath(path) if path else os.getcwd()
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
        subprocess.run([sys.executable, "-m", "pip", "install", target], check=True)
    except subprocess.CalledProcessError as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "updated", "path": target}, sort_keys=True))
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
    analyze_cmd.add_argument("--script", required=True, help="path to the local pipeline-sim .ps1")
    analyze_cmd.add_argument("--repo", help="local checkout (overrides repository.local_path)")
    analyze_cmd.add_argument("--branch", help="branch to analyze (default: config main_branch)")
    analyze_cmd.add_argument("--skip-pull", action="store_true")
    analyze_cmd.add_argument("--execute", action="store_true")
    runall_cmd = commands.add_parser("run-all", help="fetch + slice every chunk into its own queue")
    runall_cmd.add_argument("--repo", help="local checkout (overrides repository.local_path)")
    runall_cmd.add_argument("--state", required=True, help="base directory for chunk queues")
    runall_cmd.add_argument("--output", help="fetch output directory")
    runall_cmd.add_argument("--execute", action="store_true", help="create the chunk queues")
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
    update_cmd = commands.add_parser("update", help="git pull + reinstall the pack from its clone")
    update_cmd.add_argument("--path", help="SonarRemedy clone directory (default: current)")
    init_cmd = commands.add_parser(
        "init", help="write .vscode/mcp.json + the Copilot instruction into a project"
    )
    init_cmd.add_argument("--dir", help="target project directory (default: current)")
    supp_cmd = commands.add_parser(
        "scan-suppressions", help="detect code-level suppressions that may evade Sonar"
    )
    supp_cmd.add_argument("--repo", help="local checkout (overrides repository.local_path)")
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
            mcp_path, instructions_path = _write_init_files(target)
            print(
                json.dumps(
                    {
                        "status": "initialized",
                        "dir": target,
                        "mcp": mcp_path,
                        "instructions": instructions_path,
                    },
                    sort_keys=True,
                )
            )
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
        if args.command == "fetch":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            if not repo:
                raise rc.ConfigError("--repo is required, or set repository.local_path in config")
            token_env = rcfg["sonar"]["token_env"]
            if not os.environ.get(token_env):
                raise rc.ConfigError(f"{token_env} must be present in the environment")
            output = args.output or _default_output(args.project)
            result = sonar_fetch.fetch(build_fetch_config(rcfg, repo), output)
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
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "run":
            repo = args.repo or (rcfg.get("repository") or {}).get("local_path")
            import debt_queue
            import debt_runner

            work = debt_queue.Queue(
                args.state, target=repo, branch=rcfg["repository"]["main_branch"]
            )
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

            work = debt_queue.Queue(
                args.state, target=repo, branch=rcfg["repository"]["main_branch"]
            )
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

            work = debt_queue.Queue(
                args.state, target=repo, branch=rcfg["repository"]["main_branch"]
            )
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
            argv = _analyze_argv(rcfg, args.script, repo, branch, skip_pull=args.skip_pull)
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
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled", "reason": "operator_cancelled"}))
        return 130
    except Exception as error:
        secret = os.environ.get("SONAR_TOKEN", "")
        if isinstance(error, (rc.ConfigError, sonar_fetch.Blocked)):
            reason = sonar_fetch.redactor(secret)(str(error))
        else:
            reason = type(error).__name__
        print(json.dumps({"status": "blocked", "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
