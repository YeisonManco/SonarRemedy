# Changelog

All notable changes to SonarRemedy are documented here.

## [0.6.4] - 2026-09-19

### Fixed

- **Snapshot tolerates locked files and skips regenerated outputs** — `configure` crashed with bare `PermissionError` on Visual Studio's locked `.vsidx` indexes, and real .NET repos exceed the 256MB snapshot budget with `bin/`/`obj` duplicates. Unreadable files now record a deterministic `unreadable:<type>` marker (a later change still mismatches and requires re-slice), and machine-regenerated dirs (`bin`, `obj`, `.vs`, `.idea`, `TestResults`, `node_modules`) are excluded from the binding — the checks themselves rewrite them, so hashing them made every build look like contamination. Declared generated files stay governed by `allowed_outputs`.

## [0.6.3] - 2026-09-19

### Fixed

- **Opaque OS errors** — raw `OSError`/`PermissionError` failures reported only the exception type (`"reason": "PermissionError"`), which cannot be diagnosed. The facade now includes the sanitized message (file/operation), so the next one names the culprit.

## [0.6.2] - 2026-09-19

### Fixed

- **`detect-checks` emits canonical paths** — it reported the `dotnet` path with the machine's PATHEXT casing (`dotnet.EXE`), which its own validator then blocked with `case_alias`. Detection now canonicalizes the repo root and the executable (same `canonical_case` as the harness), so a generated draft always satisfies validation.

## [0.6.1] - 2026-09-19

### Fixed

- **Case/short-name aliases in `configure`** — `validate_config` rejected temp-rooted targets with `case_alias` on CI (same class as the autopilot fix: system temp roots drift in case or use 8.3 short names). It now canonicalizes via `debt_queue.canonical_case` before validating, so the check runs on the true path while links and missing paths still block.

## [0.6.0] - 2026-09-19

### Added

- **`detect-checks`** (`sonarremedy detect-checks` + `sonar_remedy_detect_checks` MCP tool, read-only) — inspects a checkout and drafts its `checks.json` (.NET solution + test projects + `dotnet` with the real executable sha256), reporting `missing` judgments for the human instead of inventing values. The model flow is now detect → present → ask (policy/reason or RED markers), then the human approves via `configure --approve-checks-sha256`.

## [0.5.2] - 2026-09-19

### Fixed

- **`checks.json` authoring gap** — binding failures said only `invalid_execution_config`, which no model or human could act on. Every validator rejection now names the exact field (`checks[1].timeout_seconds`, `checks[0].argv[0]`, …), plus new `docs/checks-reference.md` with every field rule, the sha256 computation command, and the dry-run → review → approve flow. The example template is pinned by test (shape contract) and a fully substituted copy is proven valid by test.

## [0.5.1] - 2026-09-19

### Fixed

- **Case-aliased paths in `autopilot`** — system-temp and hand-typed paths whose case differs from disk made `slice` fail with `case_alias` on CI while passing locally (temp roots may also use 8.3 short names invisible to directory listings). `autopilot` now canonicalizes paths at entry (new `debt_queue.canonical_case`: short names expanded via GetLongPathNameW, on-disk case restored; links and missing tails preserved so the security checks still see and block them).
- **Hook idempotence test** — it counted the word "SonarRemedy" in the installed hook, which also matches a `SonarRemedy` checkout path (CI). It now compares installed bytes and counts the hook marker instead.

## [0.5.0] - 2026-09-19

### Added

- **Pre-push commit gate** — `init` installs a versioned pre-push hook (`.git/hooks/pre-push`) that runs the project's gates and blocks the push on red, so nobody (human, Copilot or agent) lands code with a failing build or suite, and `--no-verify` is forbidden by the instructions. Pack repos run suite + `ruff check` + `ruff format --check` (CI mirror); managed projects run commands declared in `.sonarremedy-hooks.json` (missing declaration blocks with the exact shape). `doctor` verifies the hook (`git_hooks`: ok/missing/foreign/outdated, never overwrites a foreign hook) and `--fix` reinstalls it. New `sonar_hooks` module; README documents the hook plus the GitHub branch-protection backstop.

## [0.4.0] - 2026-09-19

### Added

- **`autopilot` harness** (`sonarremedy autopilot` + `sonar_remedy_autopilot` MCP tool) — deterministic end-to-end flow with all the rules: setup-gate (repo `init` via `check`, exact `init --dir` fix), slice, manual proposal batches with `resume`, configure-gate (integrating stops until checks are explicitly bound with `--approve-checks-sha256`), integrate, and status with the exact next command. Dry-run by default; stateless stepper, so Copilot drives one command per phase instead of improvising the pipeline. The model only fills bounded proposals; invalid ones record `failed` and never apply.

## [0.3.1] - 2026-09-19

### Fixed

- **Worktree init detection** — `check` and `doctor` now verify the init-written files (`.vscode/mcp.json`, `.github/copilot-instructions.md` with the SonarRemedy section, `.github/sonarremedy-instructions.md`), not just `.sonarremedy/version.json`. A fresh worktree from `HEAD` (no uncommitted instructions, no gitignored `.sonarremedy/`) reports `not_initialized` / `init_files` with the exact fix `sonarremedy init --dir <worktree>` instead of a silent `up_to_date`. `doctor --fix` recreates the missing files idempotently (user instructions merged, `rules.json` kept). No commit/push of instructions required. Documented in README (one worktree per branch + init per worktree).

## [0.3.0] - 2026-09-18

### Added

- **`need_more_context` context escape** — a worker can defer with `reason: "need_more_context"` when the bounded source window (±8 lines for files >16 KiB) is too small for a complex fix. The queue re-opens the job and the next claim materializes the FULL file (attempt 2); a second `need_more_context` is terminal. This keeps the common case cheap while letting complex fixes request more context only when genuinely needed.
- **`rules` as an MCP tool** (`sonar_remedy_rules`) — the AI can list/manage the whitelist/blacklist when the user asks.
- **`check`** — reports whether the project's SonarRemedy setup is up to date with the installed pack (`up_to_date` / `outdated` / `not_initialized`), driving the "re-run `init` after an update" flow.
- **`doctor`** — diagnostic command + MCP tool (`sonar_remedy_doctor`): checks the project version and (with `--state`/`--repo`) a queue's identity (root/branch/revision) against the actual git state, reporting each mismatch with the EXACT fix command. `--fix` applies the safe repairs (re-runs `init` when the setup is behind the pack; never touches a queue or the git state). A bad `--state` (a `.json` file instead of the queue directory) reports a clear "must be the queue directory (contains queue.sqlite3)" hint. Documented in the README ("Diagnose" section) and in the Copilot instructions (rule 10: run `doctor`, don't guess).

### Fixed

- **`update` auto-detects the clone** — `sonarremedy update` now works from any directory (prefers the module's own location), not only from the clone folder.
- **`update` reinstalls editable (`-e`)** — a non-editable reinstall moved the module to `site-packages`, breaking auto-detection on the next run; it now stays editable so the clone is always found.
- **Fetch with zero issues** — no longer crashes with `IndexError`; writes an empty export and reports `issues_total: 0`.
- **Missing config** — `fetch` without a configured project now reports `config not found` (a clear `ConfigError`) instead of a raw `FileNotFoundError`.
- **`init` gitignores only the local state** — `.sonarremedy/` and `.vscode/mcp.json` (machine-specific) are added to `.gitignore`. `.github/copilot-instructions.md` is NOT gitignored: it is the user's own file (Copilot reads it from its standard location) and may already be tracked.
- **Full Copilot instructions moved under `.github/`** — `sonarremedy-instructions.md` now lives at `.github/sonarremedy-instructions.md`, not `.sonarremedy/instructions.md`: Copilot's file search skips gitignored paths, so a pointer into `.sonarremedy/` was unreadable. The pointer (`.github/copilot-instructions.md`) references the new path.
- **Detailed identity mismatch** — `target_identity_mismatch` now reports WHICH field (root/branch/revision) and its expected vs actual values, so the operator (or Copilot) sees the exact discrepancy instead of a bare `Blocked`.
- **Queue commands no longer re-assert the config's branch** — `run`/`configure`/`integrate` passed `branch=config.main_branch` as a redundant assertion, which blocked a queue whose bound branch was correct but whose config had drifted (e.g. `main` vs `hotfix/correccion-sonar`). They now assert the repo path only; `check_identity` validates the ACTUAL git branch/revision.
- **CLI shows the real block reason** — the facade only recognized `sonar_fetch.Blocked`, so a `debt_queue.Blocked` (or `sonar_suppressions.Blocked`, etc.) reported the generic `"Blocked"` instead of the actual reason. It now recognizes every `Blocked` (by name) and reports the real message.

### Changed

- **Copilot instructions + MCP descriptions** — disambiguated `scan_exclusions` (detect in code) vs `rules` (configured lists), so Copilot scans the code instead of just listing empty rules.
- **Copilot instructions** — explicit "NEVER read the SonarRemedy source code; the `sonar_remedy_*` MCP tools are the only interface" (stops Copilot from reading the pack implementation).
- **Slim Copilot pointer + full instructions** — `.github/copilot-instructions.md` is now a short pointer; the full rules live in `.sonarremedy/instructions.md` (both written by `init`). Added the rule "always use SonarRemedy; if it's missing or a command fails, report it clearly and ask — never fall back to manual work unless the human explicitly says so".
- **Smart merge + version tracking in `init`** — `.github/copilot-instructions.md` now MERGES the SonarRemedy section (between `<!-- SonarRemedy:start/end -->` markers), preserving the user's own instructions instead of overwriting them; and `.sonarremedy/version.json` records the pack version so `init` reports when SonarRemedy was updated since the last run.

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
