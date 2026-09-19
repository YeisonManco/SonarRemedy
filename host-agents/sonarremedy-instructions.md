# SonarRemedy — full instructions

The `sonar_remedy_*` MCP tools are the ONLY interface to SonarRemedy. Read this whole file before doing any debt/Sonar work.

## 0. Always use SonarRemedy — never do the work yourself

For technical debt, Sonar issues, "deuda técnica", or "recuperá la deuda", ALWAYS drive the `sonar_remedy_*` tools. Never fall back to doing it manually:

- If SonarRemedy is NOT installed or the tools are missing → SAY so ("no encontré SonarRemedy instalado, ¿querés que lo instale?") and STOP. Do not do the work yourself.
- If a command fails (returns `blocked` or an error) → SAY so: report WHICH command failed and WHY (its `reason`), look the reason up in the Troubleshooting FAQ below, and SUGGEST the documented recovery before asking to proceed. Do NOT stop cold with just "how do you want to proceed?" — always propose the fix the FAQ prescribes.

Only if the human EXPLICITLY says "no uses SonarRemedy" (or similar) may you do the work yourself. If the human asked you to use SonarRemedy, using it is not optional.

If integration is blocked for missing reviewed checks (`reviewed_execution_config_required`): run detection (`sonar_remedy_detect_checks` with the repo), present the draft plus the `missing` list to the human, and ASK for the judgments (policy/reason or failing-test markers). Never invent exe paths, hashes, or markers.

## 1. Never read the source

NEVER read the SonarRemedy source code — not `sonar_remedy*.py`, `debt_*.py`, `sonar_*.py`, nor any file inside the pack. The MCP tools are the only interface: call them, do not inspect how they are implemented. Likewise, DO NOT analyze the target project's code manually (no reading files, no grep, no "let me check the code", no own diagnosis).

## 2. Drive the tools

USE the `sonar_remedy_*` MCP tools in this order:

- `sonar_remedy_fetch` → collect Sonar issues for the project
- `sonar_remedy_slice` → create the durable queue
- `sonar_remedy_schedule` → parallel/serial plan
- `sonar_remedy_run` → lease a proposal batch
- `sonar_remedy_status` → next action + ETA
- `sonar_remedy_progress` → human-readable progress

## 3. Config

Sonar URL / project key / token come from the SonarRemedy config. If no project is configured (check `sonar_remedy_projects`), ASK the user for the Sonar URL, project key, repo URL and local path, then save it with `sonar_remedy_configure_project`. Branch: if the URL includes `?branch=`, that branch is auto-detected; otherwise ASK the user for the main branch (never assume "main"). Never invent or hardcode Sonar values.

## 4. Token — check, don't assume

The token lives in the environment (`SONAR_TOKEN`), not the config. After the project is configured, RUN `sonar_remedy_fetch`:

- If it blocks with "SONAR_TOKEN must be present", the token is MISSING — ask the user to set it once, masked (e.g. `python sonar_remedy_config.py --project <name>`), and tell them to RESTART the editor afterwards (the MCP server only reads the environment at launch).
- If it blocks for any OTHER reason (HTTP, network, revision mismatch), the token IS already set — do NOT ask the user to set it again; proceed with the existing one.

## 5. Never explore the repo to "find" debt

Never explore the repo broadly to "find" the debt yourself — the tools already fetch the authoritative Sonar results.

## 6. Report project + branch

When reporting the debt, ALWAYS state the project name AND the branch analyzed (the `sonar_remedy_fetch` result includes `project` and `branch`).

## 7. Estimates

When the user asks how long the AGENT will take to recover the debt, use `sonar_remedy_status` / `sonar_remedy_progress` (they report `estimated_hours` = remaining jobs × minutes-per-job). NEVER invent a human-effort estimate in weeks/months — that is a different question (people fixing by hand).

## 8. Exclusions / suppressions / omissions (separate from debt)

Two DIFFERENT tools exist — do NOT confuse them:

- `sonar_remedy_scan_exclusions` — SCANS the code and DETECTS exclusions (NOSONAR, @ts-ignore, #pragma, NoWarn, coverage exclusions, …). When the user asks to "find", "detect", or "buscar" exclusions/suppressions/omissions, ALWAYS run this — it scans the repository. It reports each finding with its rule, category, and severity (HIGH/MEDIUM/LOW = the danger level).
- `sonar_remedy_rules` — only SHOWS/EDITS the configured whitelist/blacklist (rules the USER added). It is EMPTY until the user adds rules; it does NOT scan the code.

NEVER answer "there are no exclusions" from `rules` alone — run `sonar_remedy_scan_exclusions` FIRST. For suppressions only (certain vs ambiguous) use `sonar_remedy_scan_suppressions`. Only `allow`/`block` a rule when the USER explicitly asks — the whitelist is a human decision.

## 9. Exclusion correction is NOT automatic

Unlike code smells (which the proposal workers fix), an exclusion/suppression may be LEGITIMATE (a justified `@ts-ignore`, `NoWarn`, or coverage exclusion). NEVER auto-fix or auto-remove them. Report them (rule, file, line, severity) and let a HUMAN decide whether to remove or justify each one.

## 10. When something looks wrong, run `doctor` — do not guess

If a command returns a `blocked` reason you do not understand, or the setup looks off (an outdated version, a `target_identity_mismatch`, a `missing_path`):

1. Call `sonar_remedy_doctor` (optionally with `state` and `repo`). It reports what is wrong and the EXACT fix command for each mismatch (which of root / branch / revision differs, and what to run).
2. For a safe setup repair, call it with `fix: true` — it re-runs `init` when the project setup is behind the pack. It never touches a queue or the git state.
3. Report the diagnosis and the suggested command to the human. Do NOT invent a fix, do NOT do the work manually, and do NOT invent a state path: `state` is the QUEUE **directory** created by `slice` (it contains `queue.sqlite3`), not an export `.json` file.

A `target_identity_mismatch` is the pack's fail-closed safety, not a bug: the queue is bound to one checkout/branch/revision. Always use the SAME `--repo` path across `fetch` → `slice` → `run` (one worktree per branch).

## 10b. Commits and pushes go through the gate — never `--no-verify`

`init` installs a pre-push hook that runs the project's gates (suite + lint for the pack; declared commands for managed projects). The rule is absolute: NEVER commit or push with `--no-verify` or any hook bypass. A red gate blocks the push until the failure is fixed — fix the code, never silence the gate. If the hook is missing or outdated, call `sonar_remedy_doctor` (with `fix: true`) instead of pushing blind. No commit or push exists inside the queue runner; those are separate human operations that always pass the gate first.

## 11. Generating proposals — use the kind hint + language hint

When you act as the worker and generate a `proposal.json`, fix the issue using BOTH:

**Kind hint (what to fix):**
- `smells` — minimal idiomatic simplification (complexity, duplication, clearer naming/structure); preserve behavior.
- `security` — validate/escape untrusted input, least privilege, no hardcoded secrets; never weaken protections.
- `coverage` — add/adjust a focused test for the uncovered line/branch; never weaken assertions.
- `duplication` — extract the duplicated code into a shared function/constant and reuse it.

**Language hint (how to fix it idiomatically):**
- `.cs` → .NET: nullability, LINQ, analyzers; avoid `#pragma warning disable` / `[SuppressMessage]`.
- `.ts`/`.js` → Angular/React: strict typing, type guards, hooks rules; no `@ts-ignore` / `eslint-disable`.
- `.py` → stdlib + ruff/mypy idioms, f-strings, `pathlib`, type hints; no bare `# noqa`.
- `.java` → records/streams; no `@SuppressWarnings` unless justified.

For more depth, read the per-language skill (`skills/<language>-index.md`, e.g. `dotnet-index.md`) and the Sonar fix skill (`skills/sonar/sonar-fix-issue/SKILL.md`). Keep the proposal itself small and exact: the `edits` carry the exact `old → new` replacement text.

## 12. The proposal.json contract (EXACT format)

A valid proposal is a single JSON object with EXACTLY these fields — no extra, none missing:

```json
{
  "version": 1,
  "job_id": "<copied from the job>",
  "attempt_id": "<copied>",
  "lease": "<copied>",
  "context_fingerprint": "<copied>",
  "status": "proposed",
  "edits": [
    {"path": "<file>", "before_sha256": "<sha>", "replacements": [{"old": "<exact text>", "new": "<replacement text>"}]}
  ],
  "reason": "short_token_no_spaces",
  "risks": ["human-readable risk"],
  "test_plan": "one string describing the intended behavioral proof",
  "follow_up": []
}
```

Rules (a wrong field type REJECTS the whole proposal):

- `status` is only `proposed`, `deferred`, or `failed`. `proposed` REQUIRES `edits`; `deferred`/`failed` have NO edits and a `reason`.
- `reason` is a short TOKEN with NO spaces: `^[A-Za-z0-9_.-]{1,128}$` (e.g. `make_dto_nonempty`).
- `test_plan` is a SINGLE STRING (1..4000 chars) — NOT a list.
- `follow_up` is a LIST (max 4) of `{"action": "<token>", "name": "<short>", "note": "<short>"}` — use `[]` when none. It is NOT a string.
- `risks` is a list of strings (max 8).
- `edits` are max 8; each `replacements` list has max 32 items. `old` must match the file bytes EXACTLY (CRLF/newlines included).
- `version` 2 adds a `phase` field to each edit (`test` or `implementation`) for red-first work.
- To request MORE context when the bounded source window is too small for a complex fix, defer with `status: "deferred"`, `reason: "need_more_context"`, `edits: []`. The queue re-opens the job and the next claim materializes the FULL file (attempt 2). A second `need_more_context` is terminal (deferred for real).

## Troubleshooting (FAQ)

When a command fails with a blocked reason, use this table instead of guessing or doing manual work. For EVERY one: report the reason, apply the documented recovery, and SUGGEST it to the human — never stop cold without proposing the fix.

- **`target_identity_mismatch`** — the queue is bound to a different checkout/branch/revision than what is running. Run `sonar_remedy_doctor --state <queue> --repo <repo>` to see WHICH field differs and the exact fix. It is a safety block, not a bug: always use the SAME `--repo` path across `fetch` → `slice` → `run` (one worktree per branch). The queue's binding is the source of truth for the branch — do NOT re-assert the config's `main_branch`.
- **`missing_path`** — `--state` is not the queue directory. It must be the directory created by `slice` (it contains `queue.sqlite3`), not an export `.json` file.
- **Outdated project setup** — run `sonar_remedy_doctor` with `fix: true` (or `sonarremedy init`) to re-sync the instructions + version marker.
- **"No aparece `.sonarremedy/`"** — it is gitignored (local state only), so Copilot's file search skips it. The full instructions are at `.github/sonarremedy-instructions.md` (readable). Do NOT use `.sonarremedy/` to decide anything.
- **`SONAR_TOKEN must be present`** — the token is missing from the environment; ask the user to set it once (masked) and restart the editor. Any OTHER blocked reason (HTTP, 403, network, revision) means the token IS set — do NOT ask the user to set it again.

### Build / check failures

- **`baseline_build_failed`** — the configured build failed on the UNMODIFIED tree (before any fix), so the job is NOT at fault and stays `proposed` for retry. Read the attempt's `baseline-build.failed.json` (it carries `argv`, `exit_code`, `stdout_tail`) and report the exact `exit_code` + `stdout_tail` to the human. Common causes: a locked file (close Visual Studio / other dotnet processes), or the wrong .NET SDK (pin it with a `global.json` — see `path1` restore errors). After the environment is fixed, RETRY the same integrate; do NOT re-slice.
- **`configured_build_failed`** (red/green phase) — the build failed AFTER a fix was applied. Read the `.failed.json` receipt and report it; the job is deferred. This one CAN indict the proposal.
- **`baseline_check_process_failed` / `configured_check_process_failed`** — the process did not exit cleanly; read the `.failed.json` receipt and report `reason` + `stdout_tail`.

### Barrier / journal recovery

- **`target_quarantined_or_interrupted`** — an interrupted integrate left a barrier. Run `sonar_remedy_doctor --repo <path> --fix` (0.7.0+): it releases the barrier ONLY after verifying the tree still matches the pre-integration snapshot. Never hand-delete barrier files; a real `quarantine` needs the human.
- **`integration_journal_exists_manual_review_required`** — a killed integrate left its journal folder. Same recovery: `sonar_remedy_doctor --repo <path> --fix` (verified release). Do not delete it yourself.
- **`Contaminated: unexpected_target_write_or_stale_snapshot`** — the tree changed since `configure` (a new file was added, or a build ran). `configure` is one-shot per queue, so if the change is intentional (e.g. a `global.json` the human added), RE-SLICE a fresh queue (`state-<branch>-2`) so the snapshot re-captures it — then re-run configure + integrate. Report this to the human.
- **Barrier refuses right after a pack update + `init`** — if `doctor --repo --fix` (or the barrier release) refuses with `barrier_tree_changed_manual_review_required` and the only changed files are SonarRemedy's own (`.sonarremedy/version.json`, `.github/copilot-instructions.md`, `.github/sonarremedy-instructions.md`, `.vscode/mcp.json`), that is a FALSE contamination: `init` re-wrote them on the pack update, not the fix. Verify the proposal's write target is unchanged, then release the barrier. Known debt: the snapshot still includes SonarRemedy's own managed files, so this false-positive recurs on every pack update — report it, do not hand-patch the target.

### Path / OS errors

- **`case_alias`** — a path's case differs from disk (often the temp dir or `dotnet.EXE` vs `dotnet.exe`). Update the pack to 0.6.1+ (which canonicalizes) and re-run `detect-checks` / the command with the canonical path.
- **Raw `OSError` / `PermissionError`** — the reason includes the file/operation (0.6.3+). Report it and check locks/permissions (a file open in another process, read-only queue, disk full).

### .NET build/restore specifics

- **`Value cannot be null. (Parameter 'path1')` in `NuGet.targets` during restore** — the restore graph failed, NOT the code. Three known causes, in order: (1) the wrong .NET SDK is being used — pin it with a root `global.json` (`{"sdk":{"version":"8.0.319","rollForward":"latestFeature"}}`, matching the installed SDK), because a newer SDK can break `net8.0` restore; (2) the harness environment is missing a variable — fixed in the pack 0.7.2+ (forward `PROGRAMW6432`), so update the pack; (3) a locked file — close Visual Studio / other `dotnet` processes. Read the `.failed.json` `stdout_tail` and report the exact SDK line + the project that failed.
- **Build passes in a terminal but fails inside the harness** — the harness runs with a whitelisted environment; a missing variable breaks `dotnet`/NuGet. Report it as a pack defect (it was `PROGRAMW6432` once); do not hand-patch the target.
- **`Compilación correcta` / "build succeeded" in the receipt means the environment is fixed** — retry the same integrate; the job stays `proposed`.

## Documentation rule (agents — read before committing or pushing)

Every user-visible event MUST be documented in the SAME change that caused it — before any commit or push. Concretely:

- A new blocked reason, a recovery you had to apply, or a fix you (or the human) discovered → add an entry to the Troubleshooting FAQ above AND a line to `CHANGELOG.md` (under `## Unreleased` or a new version).
- A changed command, flag, behavior, or install step → `README.md` + `CHANGELOG.md` in the same change.
- Never commit or push a fix whose "why" (the failure, the cause, the recovery) is not written down. If the docs are stale, fix the docs FIRST, then commit the fix and the docs together.
- This rule applies to the pack itself and to any repo you touch on the human's behalf; the only exception is a strictly mechanical revert of a mistake you made in the same session (no new knowledge to record).

## Personality (response style)

The user may pick a response style (e.g. "con personalidad gracioso"). Default: `intelectual`.

- **grosero**: Colombian-style insults as seasoning, but ALWAYS teach the concept. Insult the code/situation, never the user. (Spanish.)
- **gracioso**: jokes, paradoxes and funny analogies tied to the answer, to teach.
- **silencioso**: only the strict output. No greetings, no filler, nothing extra.
- **intelectual**: explain like a professional engineer — precise terminology, fundamentals, and tradeoffs.
