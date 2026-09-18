# Roadmap — distribution & professional polish

## Distribution strategy (done)

1. ✅ **Git repo** — committed (84 files) and published to https://github.com/YeisonManco/SonarRemedy.
2. ✅ **`sonar-remedy init`** — writes `.vscode/mcp.json` + the Copilot instruction into a project.
3. ✅ **Package as a pip-installable CLI** — `pip install .` verified; `sonarremedy`, `sonar-remedy-mcp`, `sonar-remedy-config` entry points smoke-tested (CI step added).

## Removed / not planned

- **VS Code extension (`.vsix`)** — dropped: `sonar-remedy init` already wires the editor in one command, so a turnkey extension is redundant.
- **Headless T38 (live tool-free provider launch)** — dropped from the roadmap. The pack's code layer is done (manual provider, offline OpenCode/Codex parsers, tool-free worker templates). The remaining gap is not pack code: it needs the user's provider auth/config (a Claude token, or an OpenCode deny-all agent). Copilot CLI cannot be tool-free — its `--available-tools ""` does not remove the built-in tools.

## Professional polish (done)

- [x] **LICENSE** — MIT.
- [x] **pyproject.toml** — name `sonarremedy`, version `0.1.0`, entry points (`sonarremedy`, `sonar-remedy-mcp`, `sonar-remedy-config`).
- [x] **.gitignore** — Python + runtime data.
- [x] **README.md** — rewritten for SonarRemedy.
- [x] **CI (GitHub Actions)** — runs the tests on push/PR, installs the package, and smoke-tests the entry points.
- [x] **CHANGELOG.md** — per-version notes.
- [x] **Install docs** — README install section (pip + no-install fallback) and editor setup via `init`.
- [x] **Python skills index** — `skills/python-index.md` (mirror of `dotnet-index.md`).

### Nice-to-have (done)

- [x] **Type hints + formatting** — every production function annotated; ruff linter + formatter clean.

## Current state

- Pack: `sonar_remedy_config.py` (wizard), `sonar_remedy.py` (facade CLI), `sonar_remedy_mcp.py` (MCP server), `debt_*` (queue/integrator/runner), `sonar_*` (fetch/client).
- 302 tests green.
- Published to GitHub; editor integration (VS Code + Copilot Chat via MCP) proven against a real Sonar.
- Distribution, packaging, install docs, Python skills, and type hints + formatting are all done.
