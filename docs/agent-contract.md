# Sonar debt queue: canonical operating contract

> **`debt_work.py`'s direct CLI has been removed.** This file documents the
> behavioral contract of the queue engine (`debt_queue.py`/`debt_executor.py`)
> that `sonar_remedy.py` wraps with its safety layer — project
> auto-detection, `doctor`, and queue↔project registration. `sonarremedy`
> (via `README.md` and `host-agents/sonar-remedy-orchestrator.md`) is the
> only supported CLI onto this engine now. This document is the canonical
> contract for [work-queue.md](work-queue.md)'s commands and `proposal`
> schema below.

The safe workflow is [work-queue.md](work-queue.md), implemented by `sonar_remedy.py`, `debt_queue.py`, `debt_executor.py`, `debt_transport.py` and `debt_runner.py`. This contract takes precedence over historical direct-worker-edit/scan guidance for queue work, without weakening higher host policy. The legacy `debtpack.py` workflow remains separate; never mix its state or evidence with the queue.

## Authorization and ownership

1. The owner declares the canonical root, branch, existing worktree, sanitized export, full approved write sets and exact local checks. One worktree per branch, never per worker. Branches are not derived from Sonar URLs. No automatic worktree creation, checkout changes, commits or pushes.
2. Development permission is not remote execution permission. Model inference and Sonar uploads each require an explicit destination, operation and credential/session. Never inspect or reuse ambient authentication. Native provider execution is currently unavailable for all four CLIs; no permissive switches, auth-mode changes, installation or general-agent fallback.
3. Queue creation/claims/report writes require `--execute`; default preview creates no files or subprocesses. Target integration additionally requires bound, human-reviewed configuration and explicit `integrate --execute` or `run --integrate --execute`. Exact executable hashes and argv/cwd/report contracts replace stack guessing.

## Proposal workers: no tools

Worker boundary: see [`host-agents/sonar-worker.md`](../host-agents/sonar-worker.md) for the full contract (no tools, no target filesystem access, no shell/Git/build/test/network/skill/subagent access, fresh context per job, source/exports/vendor material treated as untrusted data). This proposal restriction does not weaken higher host permissions.

Native permission configuration is not an OS sandbox. Manual host assistance is permitted only with demonstrably tool-free context; otherwise a human can author the JSON. VSCode uses terminal/tasks plus optional manual chat, not a Copilot Chat worker API. Templates do not install themselves or certify effective permissions.

## Script lifecycle and acceptance

- SQLite retains every supplied ordinal, grouped by exact path+kind. Duplicate IDs, malformed/unknown findings, missing rules/source and hotspots remain explicitly deferred. Priority is security, hotspots, smells, coverage, duplication; deferred work does not hide independent eligible work.
- `pending -> leased -> proposed -> applied -> locally_verified` is separate from `deferred/failed/unavailable`. Worker success means only proposed edits. `sonar_confirmed` is reserved and unsupported; never infer it from exit 0, an old issue disappearing, an empty queue or rule+path matching.
- Full write sets, including approved existing test files, lock across kinds and case aliases. The serial integrator's target-root mutex and durable interruption/quarantine markers span queue directories using this pack. All target edits/checks are serial, even when proposals were prepared concurrently.
- Before effects, persist intent and exact preimages outside the target. Bind full source snapshot, branch/root, export, attempt/lease/context fingerprint, result and configured-check hashes. Preserve original bytes/BOM/newlines and pre-existing changes; never rewrite from redacted source.
- Run positive baseline build/TRX checks. Red-first proposals use version 2 test/implementation phases: apply only approved test edits, require exact expected failing testcase names and assertion markers, then apply implementation and require GREEN plus post-checks. Compiler/infrastructure failure is not RED. Zero, skipped, stale, contradictory or missing TRX is never a pass.
- A refactor without RED needs the owner's explicit characterization policy/reason; a model cannot select that exception. Current integration requires implementation edits and existing paths; new/test-only/rename/delete work remains deferred.
- Baseline failure without source/identity contamination defers before edits. Unexpected writes, changed branch, failed integrated checks or unknown interruption effects quarantine. Stop affected target writing, preserve evidence and require human recovery. No stash/reset/clean, automatic revert, blind retry or evidence overwrite.
- Retained local verification artifacts are hashed and checked on later queue reads. Even valid local checks are not authenticated proof, a security certification or a parent review receipt.

## Scope of evidence and optional tools

Snapshot coverage includes files/directories under the target, including ignored inputs; only root `.git` metadata is excluded, with root/branch/HEAD checked separately. Sensitive input contents are hashed, not placed in progress; only approved source edits have retained preimages. Exact configured generated binary/runtime paths are the only allowed check-write exceptions, not blanket bin/obj or extension ignores. Reads/sizes/paths are bounded; unsupported snapshots block.

Job Objects control process lifetime, not filesystem/network effects. Only trusted, owner-approved offline checks may run. Credentials are not inherited by checks; raw stdout/stderr is not persisted. External effects outside the declared target/evidence scope cannot be proven absent by this pack.

RTK is optional human presentation only, never test truth or code input. CodeGraph may supply manually obtained read-only hints from an existing index; the queue performs no indexing, sync, install or service discovery. Neither has a runtime hook yet. Unknown token/cost/savings/debt-reduction values stay null.

## Sonar, security and propagation

Do not call `sonar_compact.ps1` from the safe workflow. Legacy API/scanning tools require separate explicit authorization and cannot be delegated to proposal workers. Aggregate measures cannot create coverage/duplication work; supply explicit file evidence. Hotspots need human review; never dismiss them or invent zero unreviewed items.

Rule+path only identifies a propagation candidate, not proof of the same semantic defect. Each separately approved branch gets its own queue, divergence review, worktree and fresh evidence. Never overwrite divergent files with a different branch's fix.

Strict external goals remain coverage >90%, duplication <5%, and zero smells/outstanding security/unreviewed hotspots. Never weaken assertions or introduce suppressions/exclusions to manufacture those values. Report partial/unavailable capability honestly and keep local versus external evidence distinct.
