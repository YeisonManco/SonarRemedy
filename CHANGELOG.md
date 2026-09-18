# Changelog

All notable changes to SonarRemedy are documented here.

## [0.1.2] - 2026-09-18

### Added

- **Type hints** — every function signature (parameters + return) in the 14 production modules is annotated (py3.11 builtins, `Path`, `collections.abc.Callable`).
- **Ruff** — linter + formatter configured in `pyproject.toml`; the codebase is fully formatted and lint-clean.

### Changed

- **Exception chaining** — `raise ... from None` for deliberate exception replacements.
- **Modernized formatting** — `%`-format → f-strings.
- **Test closures** — loop variables bound as default args (removes latent-closure risk).

## [0.1.1] - 2026-09-18

### Added

- **Install documentation** — README install section (`pip install .` + no-install fallback), the three entry points, and editor setup via `sonar-remedy init`.
- **`sonar-remedy init`** — scaffold `.vscode/mcp.json` + the Copilot instruction into a project.

### Fixed

- **CI on Windows** — two tests compared or passed temp paths without resolving the 8.3 short name (`C:\Users\RUNNER~1\...`) the runner exposes in `TEMP`; they now resolve via `os.path.realpath`.
- **CI packaging smoke-test** — CI now installs the package and smoke-tests `sonarremedy`, `sonar-remedy-config`, and `sonar-remedy-mcp`.

## [0.1.0] - 2026-09-18

Initial release.

### Added

- **Fetch** — pull Sonar issues/measures/quality-gate (with HTTP opt-in and automatic chunking for large projects).
- **Durable queue** — SQLite queue grouping findings by file+kind, with bounded proposal contexts and fail-closed binding.
- **Proposal-only workers** — a worker swarm that only proposes edits; a single serial integrator applies and verifies them.
- **Config wizard + project store** — secrets live in the environment, never in files.
- **Facade CLI (`sonarremedy`)** — fetch / slice / run / status / progress / schedule / analyze / run-all / configure-project.
- **MCP server (`sonar-remedy-mcp`)** — exposes every command as a tool for VS Code Copilot, OpenCode, Claude Code.
- **Packaging & CI** — `pyproject.toml` (stdlib only), LICENSE (MIT), `.gitignore`, GitHub Actions CI.
