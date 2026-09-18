# Roadmap — distribution & professional polish

## Distribution strategy (ordered by effort)

1. **Git repo** — commit everything (the pack currently has 0 commits); coworkers `git clone`.
2. **`SonarRemedy init`** — a command that writes `.vscode/mcp.json` + the Copilot
   instruction into a project automatically (collapses the current 3 manual steps
   into "clone → init → configure").
3. **Package as a pip-installable CLI** — `pyproject.toml` + console_scripts
   (`SonarRemedy`, `SonarRemedy-mcp`), so the MCP config is `command: SonarRemedy-mcp`
   (no hardcoded path). This is the "real CLI" — no folder to copy.
4. **VS Code extension (.vsix)** — turnkey, one-click install (later).

## Professional polish (before the first GitHub commit)

### Must-have

- [ ] **LICENSE** — MIT (permissive, standard).
- [ ] **pyproject.toml** — name, version `0.1.0`, description, `requires-python >=3.11`,
      console_scripts entry points (`SonarRemedy`, `SonarRemedy-mcp`, `SonarRemedy-config`).
- [ ] **.gitignore** — exclude `__pycache__/`, `*.pyc`, `.debt-state/`, `.debt-runs/`,
      `.debt-control/`, `.codegraph/`, `debt-runner.log`, `sonar.api.main.json` (real data),
      `*.sqlite3`, `.SonarRemedy/` (runtime).
- [ ] **README.md** — update to reflect the current SonarRemedy (facade CLI + MCP server +
      config wizard + the VS Code/Copilot flow), not the old `debt_work` flow.
- [ ] **CI (GitHub Actions)** — `.github/workflows/ci.yml` running
      `python -B -m unittest discover -s tests -v` on push/PR.

### Nice-to-have

- [ ] **CHANGELOG.md** — per-version notes.
- [ ] **Type hints + formatting** — `ruff`/`black` for consistency.

## Current state (as of 2026-09)

- Pack: `sonar_remedy_config.py` (wizard), `sonar_remedy.py` (facade CLI), `sonar_remedy_mcp.py`
  (MCP server), `debt_*` (queue/integrator/runner), `sonar_*` (fetch/client).
- 301 tests green.
- Editor integration (VS Code + Copilot Chat via MCP) proven end-to-end against a real Sonar.
- Headless T38 (CLI launch) documented; not yet wired (needs a tool-free provider).
