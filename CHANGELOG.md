# Changelog

All notable changes to SonarRemedy are documented here.

## Unreleased

### Added

- **Project auto-detection from the current checkout** — `resolve_project_for_checkout` (config module) matches a local checkout to exactly one saved project by canonical `local_path` or normalized git remote URL (credentials stripped, so a PAT embedded in a remote never blocks the match). `doctor` now reports which project binds to the checkout (a `project` check: `ok` / `ambiguous` / `warning`), and `_load_config` auto-selects the matching project when neither `--config` nor `--project` is passed — so a recovery driven from inside a checkout no longer runs against the wrong project. No match falls back to the default config; an ambiguous match fails with a clear "pass --project" error.
- **Config collision detection** — `list_project_collisions` finds saved projects that share a `local_path` or normalized `repository.url`, and `doctor` surfaces them as a `project_collisions` warning, so two configs can never silently target the same checkout.
- **Embedded-credential detection** — `doctor` now reports a `remote_credentials` warning when the checkout's `origin` remote URL embeds credentials (a PAT baked into the URL), without ever echoing the token; the fix is to re-set the remote without the token and use a credential manager / env PAT.
- **Project ↔ queue index** — `register_queue` / `project_for_queue` / `queues_for_project` keep a `~/.sonar-remedy/queues.json` index of which state directory belongs to which project. `slice --execute` registers the queue on creation, and `doctor --state <queue>` reports the bound project (`queue_project`), so the agent can recover the project↔queue mapping it lost.
- **Optional TypeSafe pre-check before integration** — a new stdlib-only `typesafe_client.precheck_proposal` asks TypeSafe's System One a cheap yes/no question ("does this diff plausibly address the Sonar rule?") right before `integrate()` runs its baseline build, and records the answer as evidence (`typesafe-precheck.json` in the integration folder). It is opt-in via `TYPESAFE_API_KEY`, purely advisory, and never gates, blocks or changes `integrate()`'s outcome: with no key set (the default) it makes no network call at all, and any transport failure degrades to a recorded `error` status instead of raising.
- **Optional TypeSafe legitimacy scoring for exclusion/suppression triage** — `sonar_exclusions_report.scan()` and `sonar_suppressions.scan()` now attach an advisory `typesafe_legitimacy` field (via a new `typesafe_client.legitimacy_score`) to their most-severe still-open findings — pending exclusions in the first, ambiguous suppressions in the second — so a human reviewing NOSONAR/`@ts-ignore`/pragma-style directives can triage the least-justified-looking ones first. It is opt-in via `TYPESAFE_API_KEY` (zero network calls and zero output change when unset), bounded to at most `MAX_SCORED` (20) calls per scan regardless of findings count, never scores an already-settled finding (`blocked` exclusions, `certain` suppressions), and never auto-blocks or auto-allows anything — purely advisory data for a human, matching the existing "exclusion correction is not automatic" rule. `typesafe_client.precheck_proposal`'s HTTP/request handling was refactored into a shared, reusable `_ask_noul` helper (no change to `precheck_proposal`'s own behavior).
- **Exclusion/suppression scan truncation is now disclosed** — `sonar_exclusions_report.scan()` (cap 500) and `sonar_suppressions.scan()` (cap 200) previously stopped silently once their findings cap was hit, with the `notice` only reporting the count found and no signal that unscanned files/directories might remain. Both now return a `truncated` boolean and say so plainly in `notice` when true (e.g. "... (stopped early at the 500-finding cap, more may exist)").
- **Optional TypeSafe hotspot risk triage at intake** — a `hotspots`-kind issue is always deferred for human review by `debt_queue.plan()` and never reaches a worker (`claim()` only ever selects `pending` jobs), so this is advisory data for the human doing that review, not a worker-facing precheck. A new `typesafe_client.hotspot_risk_score` asks System One a cheap yes/no question ("does this pattern look like a genuine, exploitable risk, or a likely false positive?") for each deferred `hotspots`-kind job and attaches the answer as an advisory `typesafe_hotspot_risk` field on the job record, which `Queue.document()` now surfaces in `report.json`. It is opt-in via `TYPESAFE_API_KEY` (zero network calls and zero job-shape change when unset), bounded to at most `MAX_SCORED_HOTSPOTS` (20) calls per `plan()` call regardless of export size, never scores a `security`-kind job (even one also deferred for human review), and never gates/blocks/defers a job itself — the existing defer-for-human-review decision is untouched. `plan()` never opens a database connection or holds a lock (a queue's SQLite state is only created/opened by `slice_queue()` after `plan()` returns), so this network call never risks holding a transaction open.

### Fixed

- **`sonar_fetch`'s primary issues fetch had no retry** — the secondary calls in `fetch()` (hotspots, coverage, duplication, gate conditions) already caught `(Blocked, HTTPError)` and degraded gracefully, but the primary `api/issues/search` pagination call was not wrapped at all, so a single transient failure (network blip, timeout, or a 502/503 mid-pagination) aborted the entire fetch and discarded all progress. Issues cannot degrade away like the secondary data can. A new `_with_retry` helper now wraps that call with up to 3 attempts and short exponential backoff, but only for transient failures (network/timeout/5xx/429); a 401/403 or a genuine `Blocked` validation error still fails fast on the first attempt, never retried.
- **Sonar auth errors and generic errors were indistinguishable in `sonar_fetch` output** — `main()` reported the `reason` for any non-`Blocked` exception as just `type(error).__name__` (e.g. `"HTTPError"`), so a 401 (expired token) and a 500 (Sonar down) looked identical in the CLI/MCP output. `main()` now reports `f"http error {error.code}"` for an `HTTPError` specifically (status code only, never the raw response body/message), following the same pattern as `typesafe_client._safe_reason`.
- **Unlocked read-modify-write race on the queue registry** — `register_queue` read the whole `~/.sonar-remedy/queues.json` (global, shared across every project on the machine), mutated it in memory, and wrote it back with no lock, so two concurrent registrations (e.g. two `slice --execute`/`recover` runs for two different projects) could race and one registration could silently clobber the other's. `register_queue`'s whole read-modify-write is now serialized by a portable, stdlib-only, atomic exclusive-create lock file (`_queue_registry_lock`), so concurrent registrations for different projects/queues both persist.
- **`context_resolver.enclosing_block` was confidently wrong for non-brace languages** — for a language with no brace syntax (Python, VB.NET), brace depth stays 0 everywhere, so the "not inside any brace block" branch returned an arbitrary fixed-size window with `partial=False`: a confident claim of a correct, complete context window when the resolver actually had no real signal for the true boundary. `enclosing_block` now accepts an optional `path`, and at brace-depth 0 marks the result `partial=True` unless `path`'s extension is a known brace-delimited language — so downstream code (including the current, unwired caller in `debt_queue.py`, which never passes `path`) gets an honest `partial=True` by default instead of a silently-confident `False`.
- **`recover` crashed on its own documented resume flow** — `recover` sliced every cycle's queue unconditionally, so re-running the exact same command after it returned `awaiting_proposals` (its own documented next step) restarted the loop at `cycle-0` and crashed with `Blocked: queue_already_exists`, because that directory already held the queue from the first run. `recover` now checks for an existing `queue.sqlite3` before slicing a cycle, mirroring `autopilot`, and resumes the existing queue instead of re-slicing it.
- **Project auto-detection resolved against the wrong directory** — `_load_config`/`_resolve_project_name` matched the process's current working directory against saved projects, but every subcommand (`fetch`/`slice`/`run`/`recover`/etc.) is documented to run from the pack directory while passing the real target via `--repo`, so `cwd` was never the checkout and auto-detection never fired (including via MCP, where `cwd` is the editor's directory). They now resolve against the explicit `--repo` target when one was given, falling back to `cwd` only when it wasn't.
- **`recover`'s queues were never registered in the project↔queue index** — only the `slice` CLI branch called `register_queue`; `recover`'s own per-cycle queues never did, so `doctor --state <queue>` always reported "not in the queue index" for a `recover`-created queue. `recover` now registers each newly created cycle queue against the resolved project.
- **`--config`-based invocations skipped queue registration entirely** — `_resolve_project_name` returned `None` whenever `--config` was set, even when the config's own `repository` unambiguously matched a saved project (a common CI pattern for `slice --execute --config <path>`). A new `resolve_project_for_repository` (config module) now matches a `--config` file's `repository.local_path`/`url` against saved projects, so those queues get registered too.
- **`recover` `case_alias` on Windows CI** — `recover` canonicalized `repo` via `canonical_case` but read `checks_path` and materialized `state_base` with their raw case, so a Windows temp-dir case drift (the GitHub Actions runner's temp path) surfaced as `Blocked: case_alias` in `read_bytes`. `recover` now canonicalizes `checks_path` and `state_base` the same way it already canonicalizes `repo`, matching `autopilot`.

## [0.9.3] - 2026-09-20

### Changed

- **Drive-to-completion Copilot prompts** — README now has four copy-paste prompts (start → write proposals → keep going → finish) so Copilot runs the whole `recover` loop to completion instead of stalling, plus the exact `proposal.json` contract inline.

## [0.9.2] - 2026-09-20

### Added

- **Enclosing-scope context resolver** — for files over 16 KiB, the proposal window is no longer a blind ±8-line slice; it now resolves the enclosing brace block (method/class) for each finding and sends that bounded scope (default 120 lines, string/comment-aware). The worker sees the types, parameters and surrounding logic it needs to produce a correct fix, still capped so the token budget holds. Oversized blocks are centered and flagged partial (`need_more_context` still materializes the full file).

## [0.9.1] - 2026-09-19

### Fixed

- **`recover` missing base directory** — it sliced cycle queues under a base dir it never created, so `slice_queue` blocked with `missing_path`. `recover` now materializes the base directory before slicing.
- **`recover` branch mismatch** — it sliced to `config.main_branch` while the checkout was on a different branch (e.g. a hotfix), so `git_identity` blocked with `target_identity_mismatch`. `recover` now accepts `--branch` (CLI and MCP) to recover on the actual checkout branch without mutating the config.

## [0.9.0] - 2026-09-19

### Added

- **`recover` loop** (`sonarremedy recover` + `sonar_remedy_recover` MCP tool) — iterates the debt-recovery cycle until done or impossible: fetch → slice → run (manual proposals) → configure → integrate → status, and on `re_scan_required` re-analyzes (publish to Sonar) and starts the next cycle. The checks sha is approved once and reused across cycles. Stops on 0 issues, no progress (everything terminal), or when the model must write proposals.

## [0.8.3] - 2026-09-19

### Fixed

- **`analyze` uses the pack's built-in script by default** — `--script` is now optional (`sonar_remedy_analyze.script` too): when omitted it runs the pack's `sonar_compact.ps1`, so the AI can regenerate+publish Sonar results without anyone passing a path. Also fixed the parameter mismatch — `analyze` now passes `-WorktreePath` (matching the scripts) instead of the previously mismatched `-ProjectBaseDir`.

## [0.8.2] - 2026-09-19

### Added

- **Granular fetch by category** — `fetch` (CLI `--kinds`, MCP `sonar_remedy_fetch.kinds`) now pulls only the requested finding sources: `smells`, `security`, `hotspots`, `coverage`, `duplication` (comma-separated, default all). So you can fetch just coverage, just duplication, just hotspots, or any combination — without pulling the rest.

## [0.8.1] - 2026-09-19

### Added

- **Quality-gate thresholds** — `fetch` now reads the project's quality gate conditions (`api/qualitygates/get_by_project`) and reports them as `gate_conditions` (per metric: `op` + `error`), so the harness and the AI know the recovery TARGET (e.g. coverage ≥ 90, duplication < 5), not just the current value. Degradable with a warning.
- **Duplication as measurable jobs** — `fetch` pulls per-file duplication (`duplicated_lines`, `duplicated_blocks`, `duplicated_lines_density`) and synthesizes a `duplication` finding per duplicated file, so `slice` produces one measurable `duplication` job per file. Degradable like coverage.

## [0.8.0] - 2026-09-19

### Added

- **Coverage as measurable jobs** — `fetch` now pulls per-file coverage from the component tree (`api/measures/component_tree`) and synthesizes a `coverage` finding per file with uncovered lines (`uncovered_lines`, `lines_to_cover`, `coverage`), so `slice` produces one measurable `coverage` job per file instead of none. Files at 100% are skipped; the lookup is degradable like hotspots (a token that can't read the tree still fetches issues/measures/gate, with a `coverage tree` warning and zero invented jobs).

## [0.7.5] - 2026-09-19

### Fixed

- **`case_alias` on CI in the barrier tests** — `snapshot` and `release_barrier` read paths without canonicalizing, so a temp root with an 8.3 short-name alias (as on the GitHub runner) failed with `case_alias`. `snapshot` and `release_barrier` now canonicalize their inputs, and the barrier tests use the canonical path, so they pass on any runner.

## [0.7.4] - 2026-09-19

### Fixed

- **`init`/pack update caused false contamination** — the snapshot bound SonarRemedy's own setup files (`.sonarremedy/`, `.github/sonarremedy-instructions.md`, `.vscode/mcp.json`), which `init` re-writes on every update, so a barrier release and the integrate both reported `unexpected_target_write_or_stale_snapshot` after a harmless `init`. Those files are now excluded from the snapshot (`.github/copilot-instructions.md` stays bound — it holds the user's own merged instructions), so pack updates + `init` no longer invalidate a queue's binding.

## [0.7.3] - 2026-09-19

### Fixed

- **Characterization baseline rejected green TRX with duplicate test names** — `parse_trx` required unique `testName`s for every policy, but overloaded/parameterized tests legitimately share a name, so a passing baseline (`150/150`, 0 failures) was rejected with `trx_counters_outcomes_or_assertion_provenance_invalid` (surfacing as `baseline_environment_failed`). The uniqueness requirement now applies only to `red-first` (where test-name → assertion-marker must be exact); characterization tolerates duplicates.
- **Baseline failure left an orphaned barrier** — the baseline re-raise skipped `barrier.finish()`, leaving an `active.json` that blocked the next integrate. It now clears the barrier before re-raising.

## [0.7.2] - 2026-09-19

### Fixed

- **Configured build checks failed NuGet restore inside the harness** — the process environment whitelist was missing `PROGRAMW6432` (the 64-bit Program Files path), so `dotnet build` under the harness failed restore with `Value cannot be null. (Parameter 'path1')` while the same build passed in a full shell. `check_environment` now forwards `PROGRAMW6432` (a folder path, no secret), so configured .NET builds restore and run identically inside and outside the harness.

### Documentation

- **Troubleshooting FAQ expanded with every real-world recovery** — new sections for build/check failures (`.failed.json` receipts, `path1` restore causes), barrier/journal recovery, path/OS errors, and .NET build/restore specifics. Each blocked reason now carries its exact action so the agent SUGGESTS the fix instead of stopping cold.
- **Document-before-commit rule** — codified in `CONTRIBUTING.md` and the Copilot instructions: every user-visible change ships its docs (CHANGELOG, README, Troubleshooting FAQ) in the SAME commit; no commit or push without writing down what changed, why, and any recovery applied.
- **FAQ: false barrier refusal after pack update + `init`** — documented that SonarRemedy's own managed files (`.sonarremedy/`, `.github/copilot-instructions.md`, `.github/sonarremedy-instructions.md`) are re-written by `init`, so a barrier release right after an update reports a false `barrier_tree_changed`; verify the fix target and release. Debt noted: the snapshot still includes those files.
- **Preflight playbook** — new section 0b in the full instructions: on every recovery request the AI must run `doctor` first, classify every check, detect existing queues/barriers, state its plan, and retry in a loop instead of stopping cold or re-slicing over recoverable state.

## [0.7.1] - 2026-09-19

### Fixed

- **Copilot stopped cold on every blocked reason** — the troubleshooting FAQ only covered old cases (`target_identity_mismatch`, `missing_path`, token), so on the real-world failures (`baseline_build_failed`, `target_quarantined_or_interrupted`, `integration_journal_exists`, `Contaminated`/stale snapshot, `case_alias`, raw `OSError`) the AI had no documented answer and just asked "how do you want to proceed?". The FAQ now covers every recovery path with its exact action (read the `.failed.json` receipt, `doctor --repo --fix` for barriers/journals, re-slice on stale snapshot, pin SDK on `path1` restore errors), and the top-level rule now requires the AI to SUGGEST the prescribed fix instead of stopping cold.

## [0.7.0] - 2026-09-19

### Added

- **Blessed barrier release** — an interrupted integrate left an orphaned `active.json` that blocked every later run with no legitimate recovery. `doctor --repo <path>` now reports it, and `doctor --repo <path> --fix` releases it **only** when the current tree matches the recorded pre-integration snapshot exactly (so "nothing was applied" is proven by the tool, not hand-deletion). Real quarantines, missing intents and any mismatch stay blocked with `manual_review_required`; nothing is touched without proof.

## [0.6.5] - 2026-09-19

### Fixed

- **Lost check-failure evidence** — when a configured build/test check failed, the harness raised `configured_build_failed` but discarded the process receipt (exit code, compiler output), leaving nothing to diagnose. Failures now persist a bounded `*.failed.json` receipt (argv, exit code, stdout tail) next to the attempt before raising.
- **Baseline failures stranded good proposals** — a build failing on the unmodified tree (locked file, red environment) deferred the job with `configured_build_failed`, but `deferred` is terminal: the proposal could never retry. Baseline-only failures now raise `baseline_build_failed` / `baseline_check_process_failed` with the job left `proposed`, so the same integrate retries after the environment is repaired. Red/green-phase failures keep the existing defer/quarantine path.

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
