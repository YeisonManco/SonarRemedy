# Accept evidence, never infer success

> **`debt_work.py`'s direct CLI has been removed.** This file documents
> evidence/validation for the queue engine that `sonar_remedy.py` wraps with
> project auto-detection, `doctor`, and queue↔project registration.
> `sonarremedy` (via `README.md` and
> `host-agents/sonar-remedy-orchestrator.md`) is the only supported CLI onto
> this engine now; see [work-queue.md](work-queue.md) for the full
> command/`proposal` contract.

## Safe queue validation (current workflow)

The SQLite queue uses [work-queue.md](work-queue.md), not the legacy envelopes below. A worker supplies a `proposed/deferred/failed` object bound to job, attempt, lease, context fingerprint, full write set and source hashes. Version 2 adds explicit test/implementation phases; no worker executes tests or edits the target.

Only the serial integrator may set `locally_verified`, after owner-approved exact commands, positive baseline checks, exact assertion-based RED (or explicit characterization policy), GREEN and post-checks. Fresh TRX must have consistent positive counts, zero failures/skips for GREEN, exact testcase outcomes, and the expected exit status. Retained artifacts are hashed; missing/tampered evidence blocks subsequent reads. No automatic `sonar_confirmed` integration exists. Baseline/target snapshots, quarantine and process trust boundaries are documented in the queue guide.

No proposal/host template authorizes direct worktree writes, scans, commits or credential use. Higher host restrictions remain in force. Do not reuse legacy `validated` or `accepted` as queue confirmation.

## Legacy validation (unchanged engine compatibility)

`debtpack.py validate` validates an **operator-attested offline envelope**. It checks fields, strict thresholds, timestamps, exact supplied current revision and existence/hash of four nonempty local reports. It does not parse reports, prove a command ran, contact Sonar or inspect Git. A trusted operator must verify report meaning, provenance, scope and current snapshot before submission.

The separate [local scan workflow](local-scan.md) collects TRX/OpenCover and Sonar API evidence. Its `evidence.json` is a different format: pass run directories to `accept-local`, not that file to offline `validate`. `accept-local` reparses retained test/coverage reports and verifies their hashes, source identity and selected issue improvement. It can return `accepted` while `global_pass` is false. Failed or missing tests never permit acceptance; security jobs require human review. Both formats remain unsigned local evidence, not authenticated proofs.

## Export schema (version 1)

This is a normalized interchange format, **not a raw Sonar API response**. Manually normalize a sanitized export. Include only outstanding issues; convert reviewed-but-unsafe hotspots into security work. Coverage and duplication gaps need explicit file/line workitems; metrics alone cannot identify them.

| Field | Required contract |
|---|---|
| `version` | Integer exactly 1; boolean rejected |
| `revision` | Exact base revision, 1–128 letters/digits/underscore/dot/hyphen |
| `issues` | List, maximum 2,000 entries |
| `issues[].id` | Unique safe token, 1–128 characters |
| `issues[].kind` | `coverage`, `smells`, `duplication`, `security` |
| `issues[].path` | Existing target-relative file; `/` separators, conservative ASCII paths; no traversal, links, drives or secret names |
| `issues[].line` | Integer 1–10,000,000; actual line existence not checked |
| `issues[].rule` | Sonar rule id (e.g. `csharpsquid:S2094`), 1–200 letters/digits/underscore/colon/dot/hyphen; required in API/fetch exports, optional in legacy exports (`plan` accepts both) |

Unknown fields are discarded. Do not include secrets even in discarded fields: the export itself is read from disk. Sorted kind/path/line/ID order makes plans deterministic. IDs bind base revision and issue coordinates. Workitems are not severity-prioritized in this slice; preselect the sanitized input accordingly.

Paths with 'token' in the name are allowed for code extensions; plain-text/config token files remain rejected as secrets.

## Evidence schema

Machine-readable field constraints: [evidence.schema.json](evidence.schema.json). The Python validator also enforces the contextual rules below without third-party dependencies.

Every field below is required. `examples/evidence.json` deliberately fails until populated from real evidence. Each report path is relative to the evidence JSON's directory, must be nonempty, <=1 MiB, and may not cross links or escape that directory. Supply sanitized report summaries if original reports exceed the limit, retaining original provenance for the human reviewer.

| Field | Required value |
|---|---|
| `revision` | Exactly equals `validate --revision`; operator verifies current target snapshot |
| `build`, `tests` | JSON boolean `true`, not strings or numbers |
| `coverage` | Finite number >90 and <=100 |
| `duplication` | Finite number >=0 and <5 |
| `smells`, `security_issues`, `unreviewed_hotspots` | Integer exactly zero; no booleans |
| `sonar_timestamp` | Unix UTC seconds, no older than 24h and not in the future |
| `validated_timestamp` | Unix UTC seconds for build/tests/coverage attestation; same freshness limit |
| `build_report`, `tests_report`, `coverage_report`, `sonar_report` | Safe relative paths to actual local evidence files |

The base revision in the plan may differ from the post-fix current revision. The operator supplies the latter explicitly; report revision must match it. All four reports must correspond to that same snapshot. A submitted revision string cannot prove the working tree is clean. Re-run verification after any target change, even if commit ID is unchanged.

## Status/result contract

`planned -> running -> submitted -> validated|blocked`; `running -> blocked`; `blocked -> running`. Local acceptance also permits `submitted -> accepted`. Only validation may set `validated`, and only local acceptance may set `accepted`. A submitted job holds the single-job slot until accepted, validated or blocked. Direct terminal transitions are rejected. A job has at most two running attempts; further work requires explicit replanning.

Validation records status, diagnostic errors, report SHA-256 hashes, evidence-envelope SHA-256, checked time and current revision in local results. These are integrity references, not authenticated proofs. Historical validation is never automatically refreshed. Plans cannot overwrite existing state. Updates use atomic file replacement; single-process/operator use is required. A crash may leave `.tmp`, which must be inspected before removal/retry.

Global quality criteria intentionally block a single slice if other debt remains. Keep its submitted work and evidence; mark blocked and continue another approved job if needed. Do not call the repository clean because a plan is empty or all selected tasks have local test results.
