# Developing the SonarRemedy pack

This pack is a **tool** for Sonar technical-debt recovery, but it is also a
**codebase** you will keep developing. When you *use* it (recover debt), it is
read-only. When you *develop* it (add a command, fix a bug), follow these rules.

## Two modes (read AGENTS.md first)

- **Using** — "recuperá la deuda de X": run the CLI, never edit the pack.
- **Developing** — "agregá/arreglá X del pack": you may edit, with TDD below.

## Development workflow (mandatory)

1. **Write a failing test first** (`tests/`), observe RED, implement minimally, observe GREEN.
2. Run `python -B -m unittest discover -s tests -v` — the whole suite must pass.
3. Mutations require `--execute`; a dry-run default never writes/spawns.
4. No commits/push here — those are separate human operations under the target repo's policy.
5. Python stdlib only — no pip dependencies (the pack must stay dependency-free).

## Code style (ruff + type hints)

- **Ruff** is the linter + formatter (config in `pyproject.toml`). Before finishing, run
  `python -m ruff format .` then `python -m ruff check .` — both must pass. Install it once
  as a dev tool (`python -m pip install ruff`); it is NOT a runtime dependency.
- **Type hints** cover every function signature (parameters + return) in the production
  modules. Use py3.11 builtins (`str | None`, `list[str]`, `dict[str, Any]`), `Path` for
  paths, and `Callable` from `collections.abc` (not `typing` — ruff's UP035 rejects it).
  Never add a runtime dependency just for a type.

## Packaging & entry points

The pack is pip-installable (stdlib only). `pyproject.toml` declares three
console scripts that map to module entry functions:

| Entry point | Module |
|---|---|
| `sonarremedy` | `sonar_remedy:main` |
| `sonar-remedy-mcp` | `sonar_remedy_mcp:serve` |
| `sonar-remedy-config` | `sonar_remedy_config:main` |

`[tool.setuptools] py-modules` lists every top-level module; **when you add a
new `.py` module, add it there or the installed package will miss it.**

To develop, install editable once (`python -m pip install -e .`) so edits apply
immediately. `python -B -m unittest discover -s tests -v` stays the test loop.

CI (`.github/workflows/ci.yml`) runs the full suite, installs the package, and
smoke-tests the three entry points on `windows-latest`.

## Architecture map

| Module | Role |
|---|---|
| `sonar_remedy_config.py` | config wizard + project store (`~/.sonar-remedy/projects/<name>.json`); secrets in env only |
| `sonar_remedy.py` | facade CLI — every user-facing subcommand lives here |
| `sonar_remedy_mcp.py` | MCP server — exposes each subcommand as a tool |
| `debt_queue.py` / `debt_work.py` | durable SQLite queue (state, binding, claims, leases) |
| `debt_executor.py` / `debt_runner.py` / `debt_transport.py` | serial integrator + batch runner + provider transport |
| `sonar_fetch.py` / `sonar_client.py` / `sonar_local.py` | Sonar API bridge (read), local scan (legacy) |
| `debtpack.py` | base module: shared constants (e.g. `MAX_ISSUES`), helpers |
| `host-agents/`, `prompts/`, `skills/` | orchestrator/worker templates, per-kind prompts, vendor skills |

## The MCP sync rule (MANDATORY)

`sonar_remedy_mcp.py` exposes every `sonar_remedy.py` subcommand as an MCP tool.
**When you add, rename, or change a subcommand's flags in `sonar_remedy.py`, you
MUST make the same change in ALL of these, in one commit:**

1. `sonar_remedy_mcp.py` — the `TOOLS` entry (name/description/inputSchema) and the
   `build_argv` branch.
2. `tests/test_sonar_remedy_mcp.py` — the `COMMANDS` list + a `build_argv` test.

`test_tools_cover_all_commands` fails if a command is missing its tool — keep it
green. The MCP server is the bridge from "run commands" to "talk to the editor";
it drifts silently if you forget it.

## Documentation sync rule (MANDATORY)

Every user-visible change ships its docs in the **same commit**:

1. **`CHANGELOG.md`** — a new command, renamed flag, or fixed bug gets an entry
   (under a new version, or `## Unreleased` between releases).
2. **`README.md`** — a new/renamed command, an install change, or a Quick-start
   step change is reflected here (Install, Quick start, editor setup, Safety
   boundaries).

No test catches a stale CHANGELOG or README — keep them in sync by hand with the
code, exactly like the MCP sync rule above.

## Language skills (keep in sync)

`skills/<language>-index.md` maps a language's debt problems to its authoritative
tools (`.NET` → `dotnet-index.md`, `python-index.md`, `angular-index.md`,
`react-index.md`), and `sonar_remedy.language_hint_for(path)` returns a short
distilled hint for that language. When you add a language (or a debt tool for
one), update BOTH the index file and `EXTENSION_LANGUAGE`/`LANGUAGE_HINTS` in
`sonar_remedy.py`. Workers fix using the kind hint (`hint_for`) AND the language
hint (`language_hint_for`), so both must stay accurate.

## Shared constants

- `debtpack.MAX_ISSUES` = the issue budget (single source of truth; imported by
  `sonar_client`, `sonar_fetch`, `debt_queue`). Change it in ONE place.
- Other budgets (`MAX_EXPORT`, `MAX_SOURCE`, `MAX_CONTEXT`, …) live in `debt_queue.py`.

## Token-efficiency principles (don't break these)

1. Workers get a **bounded** context (one `job.json`, ≤64 KiB) — never the export.
2. Per-kind fix hints are **distilled** (`sonar_remedy.FIX_HINTS` / `hint_for`), never
   the full vendor skill. Keep them <600 chars.
3. Reports (`monitor`/`status`/`progress`) return **counts**, not source or issue lists.
4. All runtime data (config, queues, exports) lives in `~/.sonar-remedy/`, **never** in
   the pack folder — keep the pack's workspace small.

## Where things are stored

- Config + projects: `~/.sonar-remedy/config.json`, `~/.sonar-remedy/projects/<name>.json`.
- Fetch exports: `~/.sonar-remedy/runs/<project>/` (or `--output`).
- Queues: a user-chosen `--state` directory (outside the target repo).
- Secrets (`SONAR_TOKEN`, `GIT_PAT`): environment only — never in files, args, or prompts.

## Runtime integration (VS Code / Copilot Chat)

Most users are on VS Code + Copilot Chat (not a headless CLI). The integration is
the **MCP server** (`sonar_remedy_mcp.py`), which Copilot Chat calls natively.

1. Copy `host-agents/vscode-mcp.json` to the project's `.vscode/mcp.json`
   (adjust the `args` path to the pack's `sonar_remedy_mcp.py`).
2. Reload VS Code. In Copilot Chat, the `sonar_remedy_*` tools become available.
3. The user just says "recuperá la deuda de X" and Copilot drives the tools.

The MCP server is the user-facing bridge; keep it in sync with the CLI (see the
MCP sync rule above). Headless CLI launches (OpenCode/Claude/Copilot CLI) are a
separate, lower-priority path for "run without an editor" — see the tracker.
