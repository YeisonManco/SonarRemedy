# Roadmap — distribution & professional polish

## Distribution strategy (ordered by effort)

1. ✅ **Git repo** — committed (84 files) and published to https://github.com/YeisonManco/SonarRemedy.
2. ✅ **`sonar-remedy init`** — writes `.vscode/mcp.json` + the Copilot instruction into a project.
3. ✅ **Package as a pip-installable CLI** — `pip install .` verified; `sonarremedy`, `sonar-remedy-mcp`, `sonar-remedy-config` entry points smoke-tested (CI step added).
4. 🔲 **VS Code extension (.vsix)** — turnkey, one-click install (later).

## Professional polish (done)

- [x] **LICENSE** — MIT.
- [x] **pyproject.toml** — name `sonarremedy`, version `0.1.0`, entry points (`sonarremedy`, `sonar-remedy-mcp`, `sonar-remedy-config`).
- [x] **.gitignore** — Python + runtime data.
- [x] **README.md** — rewritten for SonarRemedy.
- [x] **CI (GitHub Actions)** — runs the tests on push/PR, installs the package, and smoke-tests the entry points.
- [x] **CHANGELOG.md** — per-version notes.
- [x] **Install docs** — README install section (pip + no-install fallback) and editor setup via `init`.

### Nice-to-have (pending)

- [ ] **Type hints + formatting** — `ruff`/`black`.

## Current state

- Pack: `sonar_remedy_config.py` (wizard), `sonar_remedy.py` (facade CLI), `sonar_remedy_mcp.py` (MCP server), `debt_*` (queue/integrator/runner), `sonar_*` (fetch/client).
- 302 tests green.
- Published to GitHub; editor integration (VS Code + Copilot Chat via MCP) proven against a real Sonar.
- Pending: VS Code extension, headless T38 (tool-free provider), type hints/formatting.
