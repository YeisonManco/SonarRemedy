# Manual proposals, serial integration, honest evidence

> **`debt_work.py`'s direct CLI has been removed.** This document is the
> canonical reference for the queue engine (`debt_queue.py`/`debt_executor.py`)
> that `sonar_remedy.py` wraps with project auto-detection, `doctor`, and
> queue↔project registration. `sonarremedy` (via `README.md` and
> `host-agents/sonar-remedy-orchestrator.md`) is the only supported CLI onto
> this engine now; its `defer`/`reconcile`/`document`/`configure`/`integrate`/
> `run` commands map directly to the `debt_queue.Queue` methods of the same
> name described below. The lower-level `next`/`claim`/`complete`/`monitor`
> primitives have no standalone CLI (they remain Python-only, driven
> internally by `sonarremedy run`/`status`/`progress`) — this document keeps
> describing them as the engine's Python API for advanced/manual use. The
> `proposal` contract below is shared by every caller of this engine
> (`sonar_remedy.py` drives the identical `debt_queue.py` validator).

The queue engine retains every supplied issue ordinal in SQLite, groups work by exact path and kind, leases immutable bounded contexts, and records proposals. Default operations do not edit the target. Explicitly configured `sonarremedy integrate --execute` or `sonarremedy run --integrate --execute` can apply existing-file replacements and run serial local checks. Nothing launches a native model provider, commits, scans or confirms a Sonar finding. Manual JSON proposals work without any model runtime.

## Quick path

Use an existing, explicitly selected Windows worktree and a sanitized version-1 export whose revision equals that worktree's HEAD. These are examples, not authorization to operate on any real target. The queue's `--state` directory must be a **new** directory outside the target, with an existing parent. No credentials are needed.

```powershell
$Target = 'C:\work\approved-worktree'
$Q = 'C:\queue-state\run-001'
$Export = 'C:\queue-state\export.json'

# Default is read-only preview: no Git subprocess, state, logs, or provider.
sonarremedy slice --state $Q --target $Target --branch feature/debt --export $Export

# Explicit opt-in creates queue-local files and reads Git identity; no Git mutation.
sonarremedy slice --state $Q --target $Target --branch feature/debt --export $Export --execute

# `next`/`claim`/`complete` have no standalone CLI: `sonarremedy run` drives
# them internally per batch. A human or restricted adapter supplies each
# proposal, bound to the batch's claim; proposals record structured edits,
# they do NOT apply them.
sonarremedy run --state $Q --provider manual --execute

sonarremedy status --state $Q
sonarremedy document --state $Q --execute
```

Exit `0` means the requested operation completed, including explicit dry-run/deferred/no-eligible results; it never means Sonar debt was reduced. Exit `2` covers blocked, unavailable, quarantined or reconciliation-required outcomes. Every mutation defaults to dry-run. Read-only queries always use SQLite read-only mode and spawn no processes. Only explicit integration commands authorize target effects using separately bound checks; no flag authorizes remote work.

## Commands and stable core API

`sonarremedy <command> --help` documents every CLI-exposed command's flags; `--state` precedes the queue directory argument on each. The table below documents the underlying `debt_queue.Queue` Python API in full — the "CLI" column shows which primitives `sonarremedy` exposes directly and which remain internal.

| Queue method | CLI | Behavior |
|---|---|---|
| `slice_queue(target, export, state, branch, *, write_sets=None)` | `sonarremedy slice` | Preview/create the entire intake, never the first eight only. Output contains counts, not all issues. |
| `next(limit=1..8)` | *(internal only, driven by `sonarremedy run`)* | Read the next eligible stage, counting distinct files and excluding intersecting full write sets. Default 4. |
| `claim(job_id=None, lease_seconds=1..1200)` | *(internal only, driven by `sonarremedy run`)* | Transactionally claim one eligible job and materialize its attempt context. Returns identity/lease/fingerprint, expiry, and `context_path`; launches nothing. |
| `complete(proposal)` | *(internal only, driven by `sonarremedy run`)* | Validate an exact bound proposal and record an immutable script envelope. Only `proposed`, `deferred`, or `failed` are accepted. |
| `defer(job_id, reason)` | `sonarremedy defer --job ID --reason CODE` | Defer pending/proposed work. A leased job instead needs its exact completion/reconciliation identity. |
| `reconcile(receipt, effects=none\|unknown)` | `sonarremedy reconcile --receipt FILE --effects none\|unknown` | Resolve an expired lease using saved claim JSON. Never requeue automatically. |
| `monitor()` | `sonarremedy status` / `sonarremedy progress` | Return compact counts by job/entry state and kind, supplied-data coverage, quarantine/gap indicators, and unavailable statistics as `null`. |
| `document()` | `sonarremedy document` | Regenerate deterministic `progress.json` and `report.json`, including every ordinal and attempt evidence locator; requires `--execute` to write. |
| `debt_executor.configure(work, config, approved_sha256=...)` | `sonarremedy configure --checks FILE [--approve-checks-sha256 HASH]` | Preview the canonical config digest; with matching reviewed digest and `--execute`, bind exact checks/executable hashes and the current target source snapshot. No checks run during configuration. |
| `debt_executor.integrate(work, job_id)` | `sonarremedy integrate --job ID` | With `--execute`, serially apply one recorded proposal and run the bound baseline/RED/GREEN/post-check contract. |
| `debt_runner.run(work, provider="manual", ...)` | `sonarremedy run [--resume] [--integrate] [--limit N] [--max-batches N] [--wall-seconds N] [--inbox PATH]` | With `--execute`, claim/process bounded manual-file batches; explicit `--integrate` also requests target effects. Native providers return unavailable before dispatch. |

Python entrypoints in `debt_queue.py`:

- `plan(target, export, branch, *, write_sets=None)` returns the bulk in-memory intake; no writes/subprocesses.
- `slice_queue(target, export, state, branch, *, execute=False, write_sets=None, identity_reader=None)` creates the bound database only with opt-in.
- `Queue(state, *, target=None, branch=None, identity_reader=None)` exposes `next`, `claim`, `complete`, `defer`, `reconcile`, `monitor`, and `document` as above. Mutation methods have `execute=False`; lease methods also accept a test-injected `now`.
- `identity_reader(root)` returns exactly `{root, branch, revision}`. Production defaults to bounded, read-only Git commands after an existing `.git` marker is confirmed. Tests inject identity without initializing Git. There is **no CLI switch** bypassing Git identity.
- `Blocked` denotes an unsafe/incomplete operation. Callers must also handle I/O failures. `sonarremedy` emits sanitized blocked outcomes rather than raw source or exception bodies.
- `debt_executor.configure(work, config, approved_sha256=..., execute=False)` and `integrate(work, job_id, execute=False)` bind and execute owner-reviewed checks. `debt_runner.run(...)` handles manual batch/resume boundaries. Internal identity/process/factory/control-root callbacks exist only for trusted isolated tests; no CLI callback, arbitrary provider argv or permission-verification boolean is exposed.

The binding includes canonical root, branch, revision, export SHA-256 and entry count. Each job includes complete `issues[]` with ordinals, an approved full write set, and baseline source hashes. Each attempt adds an ID, lease, context fingerprint and retained context SHA-256. Hashes are integrity references, not authenticated proof.

## Grouping, stages, and contexts

All 0-based export ordinals remain represented, including malformed entries and duplicate IDs. Unusable fields retain an item hash and reason instead of leaking raw messages. Duplicate IDs and case aliases are ambiguous: affected work is deferred, not silently deduplicated. A group containing unusable issues is conservatively deferred as a whole.

Priority is `security -> hotspots -> smells -> coverage -> duplication`. Deferred/locked groups do not prevent independent eligible work in later stages. Hotspots and explicitly ambiguous security evidence defer for human review. Aggregate metrics alone create no coverage/duplication work: explicit normalized file/line findings are required. Scheduling currently covers stage order and full-write-set exclusion; semantic dependency analysis and integration gates belong to the later executor.

By default the write set is only the primary file. `--write-sets` accepts a JSON object such as `{"src/A.cs":["src/A.cs","tests/ATests.cs"]}`. Every path must be explicitly approved and already exist. Shared additional paths lock across job kinds too. New-file edits, renames, deletions, binary content, and guessed test paths are unsupported in this core.

Only selected attempts receive source. Files up to 16 KiB use a full exact text window. Larger UTF-8 files use merged, exact line-numbered windows around supplied issue lines, plus the **full-file hash** and `whole_file: false`. Large additional files without issue coordinates defer because a relevant window cannot be guessed. A group that cannot fit the total 64 KiB context is deferred intact; it is not partially dispatched. A 1,205-issue same-file group may therefore be retained but too large to lease.

## Proposal and immutable result contract

Use [the proposal schema](debt-proposal.schema.json) and [the deliberately non-executable example](../examples/debt-proposal.example.json). The standard-library validator enforces the contract without a JSON Schema dependency.

Copy `job_id`, `attempt_id`, `lease`, and `context_fingerprint` exactly from the claim. `version` is integer 1 or 2. Version 2 requires each edit's `phase` to be `test` or `implementation`; version 1 has no phase and is eligible only for explicitly owner-configured characterization integration. Include `status`, `edits`, `reason`, `risks`, `test_plan`, and `follow_up`; unknown fields (including `all_fixed`) are rejected, and `follow_up` is REQUIRED — use `[]` when there is nothing for a human to do. `follow_up` is a list (max 4) of `{"action": "<token>", "name": "<short>", "note": "<short>"}` for human actions the fix requires. `test_plan` describes intended proof, not observed test execution. Each path appears once across both phases.

For `proposed`, each edit supplies an approved `path`, matching `before_sha256`, and non-overlapping `{old,new}` replacements. Old text must be nonempty, uniquely matched in the full current file **and contained in a retained window**. Every approved source hash must still match. Proposed edits are merely validated data; no file is changed. `deferred`/`failed` require a reason and no edits. `success`, `partial`, completion booleans, and verified/confirmed statuses are not accepted.

Scripts publish `jobs/<job-id>/attempts/<attempt-id>/job.json` and immutable `result.json`. The result envelope adds script-owned binding/context hashes. Workers must never write these artifacts. SQLite is authoritative; `progress.json` and `report.json` are regenerable views, not state inputs. Reports omit source, raw messages, and lease values.

## Configured integration and manual batching

Review [the deliberately incomplete check example](../examples/debt-checks.example.json). Supply the exact target/branch, native executable SHA-256 hashes, argv arrays, cwd, report filenames and timeouts. No discovery or shell splitting guesses a stack command. `{target}` and `{run}` are the only substituted argv placeholders; `{run}` is a unique evidence directory outside the target. The example is for VSTest/TRX, not Microsoft.Testing.Platform output. Every field rule is listed in [checks-reference.md](checks-reference.md); violations name the exact field.

Each TRX check must produce one complete report for its explicitly declared scope. Do not use a shared `LogFileName` over multiple projects/frameworks and accept the last overwritten file. The example deliberately selects one test project/framework; replace those values, do not assume them. Configure every mandatory suite explicitly. Current red-first integration supports one exact TRX check; multi-report RED aggregation/Microsoft.Testing.Platform support remains unavailable, so defer if mandatory checks cannot fit this contract. Characterization can use multiple separately configured TRX checks. See the official [.NET test-runner migration documentation](https://learn.microsoft.com/dotnet/core/testing/migrating-vstest-microsoft-testing-platform).

```powershell
sonarremedy configure --state $Q --checks $Checks
# Review the complete file and the canonical checks_sha256 printed above.
sonarremedy configure --state $Q --checks $Checks --approve-checks-sha256 '<reviewed digest>' --execute
sonarremedy run --state $Q --provider manual --execute
# Supply strict JSON at each returned proposal_path, using that attempt's job.json.
sonarremedy run --state $Q --provider manual --resume --integrate --execute
```

The approval digest hashes canonical JSON (the preview prints it), not arbitrary file whitespace. Do not blindly copy a model-proposed configuration/digest: these checks execute trusted repository code with local filesystem/network capabilities. Configuration is immutable for that queue; changed approval/snapshot needs a new reviewed run rather than silent rebinding. `-Resume` never resumes a model conversation.

Manual mode leases at most the selected batch, returns context/response locators and pauses immediately for missing responses. Place each response at `<inbox>/<attempt-id>.json` (default `<queue>/inbox`). Resume consumes the bound response once. Malformed/partial/wrong-identity/oversized results become explicit script-recorded failures without target edits; expired attempts require reconciliation, not resubmission. All deferred work reaches quiescence, not a false global-clean verdict. Native model automation remains unavailable; no permissive fallback is used.

Red-first policy requires exact `test_paths` and `expected_red` testcase-name/assertion-prefix mappings. Baseline build and fresh positive TRX must pass. Version 2 test edits are applied first; RED requires exit 1, exactly the configured failures and assertion markers, positive executed counts and zero skips. Compiler/infrastructure errors are not RED. Implementation edits then require GREEN and another complete post-check pass, all serial. Version 1 behavior-preserving refactors need explicit `policy: characterization`, a nonempty owner rationale and no fabricated RED claim. New-file and pure test-only integration are currently deferred; implementation edits are required.

The integrator retains intent/preimages before effects. A target-root Windows mutex spans queue directories; durable interruption/quarantine markers are stored under this pack's `.debt-control/<root-hash>/`, outside the target. All queues for the same target must use the same pack/control authority. A crash leaves the active marker blocking later integration. Source/identity contamination or an integrated failure preserves the dirty target and evidence, quarantines it, and stops writing. No cleanup/reset/revert command is provided. Baseline failure with a verified unchanged source snapshot defers before patching.

Snapshots hash all target files/directories, including ignored and sensitive inputs, without copying their contents into progress. Only root `.git` metadata is excluded; root/branch/HEAD are checked separately, so Git-index/config contents are not a hermetic part of the snapshot. No blanket bin/obj ignores: only individually declared generated `.dll`, `.pdb`, `.cache`, `.deps.json` and `.runtimeconfig.json` paths under bin/obj/artifacts may change during checks. Source/test paths cannot be those exceptions. Large/unreadable/linked snapshots fail closed.

Configured checks run in suspended Windows processes assigned to kill-on-close Job Objects before resuming. Deadlines include elapsed launch/I/O time, with bounded cleanup grace; output is capped in memory and raw stdout/stderr is not saved. Only a small noncredential environment allowlist is inherited; neither SONAR_TOKEN nor inference credentials are forwarded. Retained TRX/check/preimage/verification hashes are validated on subsequent queue reads. `locally_verified` is not authenticated evidence, security review or Sonar confirmation.

## Recovery and limits

| Situation | Safe behavior |
|---|---|
| Expired lease | Still occupies its write set until explicit reconciliation via `sonarremedy reconcile --receipt FILE --effects none\|unknown`. `effects=none` defers; `effects=unknown` fails and quarantines the queue. Neither retries. |
| Result published, database commit interrupted | Gap is reported. Explicitly resubmit the **identical** proposal while its lease remains valid; no overwrite or replayed target effects. |
| Different immutable result, missing/corrupt context/result | Block; preserve evidence. Never overwrite or infer success. |
| Incomplete slice or orphan claim folder | Explicit blocking requiring manual review. Use a separately approved fresh queue if appropriate; no guessed cleanup/reset. |
| Source/branch/root changed | Block completion or defer stale intake; no silent context refresh or rebasing. |
| Quarantine | No new claims. Read-only diagnostics and deterministic documentation remain available. |

Limits: 1 MiB export; 2,000 ordinals; 10 MiB/source file; 8 approved write paths; at most 8 leased jobs; 1,200-second leases; schema ceiling of 2 attempts/job; 64 KiB context; 256 KiB proposal; 1 GiB run artifacts; 20,000 state entries; 32 MiB database. Bounds reject/defer explicitly, never silently truncate. The current API does not reset deferred jobs for retry; any future retry mechanism needs approval and new evidence.

**Initial filesystem support is Windows fixed local volumes only.** UNC/remote/removable drives and unverified non-Windows filesystem platforms are unavailable. Every lexical state/ancestor path is checked before SQLite opens, including sidecars; reparse/symlink/case aliases and WAL sidecars are rejected. The queue uses SQLite's rollback journal and atomic no-clobber hard-link publication, so the filesystem must support that operation. No installs or environment configuration are performed.

These checks are not a hostile-process filesystem/network sandbox, disk quota or cross-machine coordination protocol. They do not prevent arbitrary same-user filesystem races, malicious external effects of trusted check code, or prove semantic independence. Writes outside the target/evidence scope are not monitored. Target disk/artifact budgets are checked at boundaries, not enforced as an OS storage quota. Only CPython/Windows check-process containment is implemented.

Native provider execution, Claude/Copilot terminal/tool-trace parsing, new-file/test-only integration, semantic dependency analysis and Sonar confirmation remain unavailable. OpenCode/Codex parsers have strict offline synthetic native-shaped fixtures only; unknown event layouts are rejected, not guessed. RTK may be used manually for human presentation of saved compact reports, never test truth or source transformation. CodeGraph hints are optional manual read-only queries against an existing index; the queue runs no init/index/sync/install/service probe. No RTK/CodeGraph runtime hook is implemented. Token/cost/savings/debt-reduction values remain `null` without evidence.

Runner defaults: 4 files/batch (max 8), 10 batches (max 1,000), 3,600 seconds (max 86,400). A configured process is limited to 1,800 seconds and 1 MiB combined stdout/stderr, with at most approximately one second of cleanup grace. The invocation deadline further limits each check; manual waiting never launches a process. Core source/context/state limits above still apply.

## Verification

```powershell
python -B -m unittest discover -s tests -p "test_debt_queue.py" -v
python -B -m unittest discover -s tests -p "test_debt_queue_cli.py" -v
python -B -m unittest discover -s tests -p "test_sonar_remedy.py" -v
python -B -m unittest discover -s tests -v
```

Fixtures cover full ordinal retention, same-write-set exclusion, real local process claim races, contexts/hashes, expiry, publication/commit gaps, corruption, read-only behavior, deterministic reports, and the `sonarremedy` CLI (`tests/test_sonar_remedy.py`, including `document`/`defer`/`reconcile`). Identity is stubbed only inside tests. This is not authenticated provider, business-target .NET, or live Sonar evidence.
