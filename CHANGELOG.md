# Changelog

All notable changes to SonarRemedy are documented here.

## [0.2.8] - 2026-09-18

### Added

- **`rules` as an MCP tool** (`sonar_remedy_rules`) — the AI can list/manage the whitelist/blacklist when the user asks.

### Fixed

- **`update` auto-detects the clone** — `sonarremedy update` now works from any directory (prefers the module's own location), not only from the clone folder.
- **Fetch with zero issues** — no longer crashes with `IndexError`; writes an empty export and reports `issues_total: 0`.
- **Missing config** — `fetch` without a configured project now reports `config not found` (a clear `ConfigError`) instead of a raw `FileNotFoundError`.
- **`init` gitignores the local editor files** — `.github/copilot-instructions.md` and `.vscode/mcp.json` are now added to `.gitignore` (Copilot still reads them from their standard location; they just aren't pushed).

### Changed

- **Copilot instructions + MCP descriptions** — disambiguated `scan_exclusions` (detect in code) vs `rules` (configured lists), so Copilot scans the code instead of just listing empty rules.
- **Copilot instructions** — explicit "NEVER read the SonarRemedy source code; the `sonar_remedy_*` MCP tools are the only interface" (stops Copilot from reading the pack implementation).

## [0.2.7] - 2026-09-18

### Added

- **`rules`** — manage the exclusion whitelist/blacklist: `rules list`, `rules allow <rule>`, `rules block <rule>`, `rules remove <rule>` (stored in `.sonarremedy/rules.json`).

## [0.2.6] - 2026-09-18

### Added

- **`scan-exclusions`** (`sonar_remedy_scan_exclusions`) — detects Sonar exclusions/suppressions by language (17 rules), categorized into sonar exclusions / NOSONAR suppressions / coverage exclusions / technical exceptions, with per-project whitelist/blacklist from `.sonarremedy/rules.json`.

## [0.2.5] - 2026-09-18

### Added

- **`init` idempotent + `.sonarremedy/`** — creates `.sonarremedy/rules.json` (whitelist/blacklist), `queues/`, `runs/`, `temp/`, and adds `.sonarremedy/` to `.gitignore` (only what's missing; never destroys).
- **`clean`** — removes generated state (`queues/`, `runs/`, `temp/`) + the sibling `*-remedy-wtrees/` folder, keeping `rules.json`.
- **`reset`** — removes `.sonarremedy/` entirely + the sibling worktrees (back to zero).

## [0.2.4] - 2026-09-18

### Added

- **`--version`** — `sonarremedy --version` prints the installed version.
- **`configure-projects`** — interactive wizard to register multiple projects at once (name, Sonar URL, project key, token env var).
- **`update`** — `sonarremedy update` runs `git pull` + `pip install` from the clone (optional `--path`).
- **Update docs** — README "Update" section.

## [0.2.3] - 2026-09-18

### Added

- **Custom token/PAT env var names** — `configure-project` now accepts `--token-env` and `--pat-env`, so each project can use its own env var (multiple Sonar servers/accounts coexist). The config stores the NAME, never the value.

## [0.2.2] - 2026-09-18

### Added

- **`follow_up` in proposals** — workers declare the human actions a fix requires (e.g. `set_env_var`), persisted and surfaced so the operator knows what to configure (pipeline secrets, packages, CI).
- **`report` command** (`sonar_remedy_report`) — lists applied fixes and the follow-up each requires.

## [0.2.1] - 2026-09-18

### Added

- **Language skills** — `skills/angular-index.md` and `skills/react-index.md` (joining `dotnet-index.md` and `python-index.md`), mapping each language's debt problems to its authoritative tools.
- **Language detection + hints** — `language_for(path)` and `language_hint_for(path)` return a short per-language fix hint so workers fix idiomatically. The orchestrator now reads the per-language skill AND the Sonar fix-issue skill, injecting both distilled hints into the worker prompt.

## [0.2.0] - 2026-09-18

### Added

- **Suppression scan** (`scan-suppressions` + `sonar_remedy_scan_suppressions`) — detects code-level directives that may evade Sonar (`NOSONAR`, `#pragma warning disable`, `@SuppressWarnings`, `eslint-disable`, `@ts-ignore`, `# noqa`, `# type: ignore`, …), each tiered `certain` vs `ambiguous`. Mechanical detection runs first; only `ambiguous` findings need AI judgment, so no tokens are wasted on clear cases.

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
