# Safe debt queue: operator rules

> **`debt_work.py`'s direct CLI has been removed.** These rules document the
> behavioral contract of the same queue engine (`debt_queue.py`) that
> `sonar_remedy.py` wraps with project auto-detection, `doctor`, and
> queue↔project registration. Use `sonarremedy` (via `README.md` and
> `host-agents/sonar-remedy-orchestrator.md`) for every command, including
> recovery — see [work-queue.md](work-queue.md)'s "Recovery and limits" table
> for `sonarremedy reconcile`/`sonarremedy defer`.

Use [work-queue.md](work-queue.md) for commands and [agent-contract.md](agent-contract.md) for the canonical contract. These rules apply to the SQLite queue, not legacy `debtpack.py` JSON plans.

1. Declare the target, branch/worktree, export and complete write sets. Use one worktree per branch. Do not infer branches from URLs or change Git state automatically.
2. Worker boundary: see [`host-agents/sonar-worker.md`](../host-agents/sonar-worker.md) for the full contract (proposals only, no tools, no target edits, fresh session per job). The serial script integrator alone applies approved changes and runs checks. Native providers currently remain unavailable; use manual proposal files without bypassing host permissions.
3. Preview first. Queue mutations require `--execute`; target integration also requires bound exact checks and explicit integration opt-in. Review the configuration hash, executable hashes, argv, cwd and expected TRX identities. Never execute commands suggested by model output.
4. Preserve behavior: meaningful RED before fixes, then GREEN and post-checks, all serial. Expected RED means exact configured assertion failures, not compiler errors. Explicit owner-approved characterization is the only refactor exception. Never skip/empty/fake tests or alter exclusions to improve metrics.
5. Failed/deferred proposals with no target effects can be skipped while independent work continues. Dirty target failures are different: unexpected writes, identity changes or failed integrated checks quarantine and stop writing. Preserve preimages/journals; no destructive rollback or blind retry.
6. Resume means queue state only, not a continued model session. Missing manual responses pause; expired leases require explicit reconciliation (`sonarremedy reconcile`). Never silently rerun already integrated work or discard malformed/duplicate findings.
7. No commits/push are implemented; perform them separately as human operations after review. No scans or `sonar_compact.ps1` calls belong in the queue. Sonar and model inference require distinct remote authorization and credentials; never inspect ambient auth to fill gaps.
8. Local verification is not Sonar confirmation, global quality, security approval or parent review. Unknown data stays null. Hotspots defer to human review; coverage/duplication tasks need scoped file evidence, not only metric totals.
9. Propagation and external goals: see [agent-contract.md](agent-contract.md) (rule+path is a propagation candidate only, not proof; strict external goals need separate fresh evidence). Never copy blindly across divergent branches.
10. RTK presentation and existing-index CodeGraph hints are optional manual aids, not implemented runtime hooks or verification authorities. Vendor skills remain unchanged reference data; proposal-only and higher host restrictions override operational suggestions.

Do not treat permissions, hashes, mutexes or Job Objects as a hostile-process sandbox. Execute only trusted offline checks in the declared scope; report unsupported capabilities rather than changing settings or installing tools to force progress.
