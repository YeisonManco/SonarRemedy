# Collect local .NET scan evidence

> **Internal/advanced interface — bypasses `sonar_remedy.py` safety.**
> `sonar_local.py` is run directly and does not go through `sonar_remedy.py`'s
> safety layer (project auto-detection, `doctor`, queue↔project
> registration). For day-to-day debt recovery, prefer `README.md` and
> `host-agents/sonar-remedy-orchestrator.md`, which drive Sonar analysis
> (`analyze`) through that safety layer. This document remains fully
> supported for advanced/manual local-scan use.

`sonar_local.py` exposes a dry-run by default and an explicitly authorized build/test/upload path. The implementation has passed fake-process/API and loopback fixtures only. **Live scanner/server compatibility is unverified; do not treat this as production approval.** No real Sonar evidence was collected during pack verification.

## Prepare and dry-run

From the pack directory, copy `examples/sonar.local.example.json` to ignored `sonar.local.json`. Set the absolute target Git root, existing target-relative solution/project, exact project key, branch and final trusted HTTPS URL. The target needs a valid Git HEAD for execution. Configuration rejects extra fields, credentials, skip switches and non-.NET adapters.

Supply `SONAR_TOKEN` through the local process environment using your trusted secret-loading mechanism. Never put it in JSON, command arguments, prompts or a checked-in file. Even dry-run requires a nonempty token; an offline fixture may use a clearly fake value.

```powershell
rtk proxy python -B sonar_local.py sonar.local.json
```

Dry-run checks configuration and lists stages; it does not start commands, contact Sonar or write output. It does not verify installed tooling, credentials, server permissions or Git snapshot readiness.

## Explicit execution (not performed during pack verification)

Only after separate authorization for target builds and remote upload:

```powershell
rtk proxy python -B sonar_local.py sonar.local.json --execute
```

Required target tooling: compatible .NET SDK, `dotnet sonarscanner`, scanner-compatible Java (JRE provisioning is disabled), test infrastructure and XPlat Code Coverage collector producing OpenCover. This pack does not install these dependencies. The configured Sonar server must support the requested branch and Web API v1 responses; incompatible/missing data blocks collection.

Execution runs scanner begin, build, tests, report checks, scanner end/upload, bounded CE polling and API collection. Build/test may restore dependencies and execute repository-controlled code. Inspect and trust the target first. This path writes target build products, `.sonarqube` and an exclusive `.debt-scan.lock`; it is not the offline planner's read-only mode. Inspect stale locks manually after a crash.

The default output is pack-local `.debt-runs/<unique-run>/`, outside the target. `--output` can select another directory outside the target; use a pack-local directory when edits must remain within this pack. The pack must not be nested inside the scanned target with default output. Never run the adapter on an unauthorized sibling project.

## Retained evidence and failures

| File | Meaning |
|---|---|
| `evidence.json` | Status, stage, return codes, snapshot/file digests, test counters, report hashes and collected Sonar summary |
| `scanner-begin.log`, `build.log`, `tests.log`, `scanner-end.log` | Bounded redacted logs for stages reached |
| `results/**/*.trx`, `results/**/coverage.opencover.xml` | Retained test and coverage reports when generated, including failed runs |
| `sonar.json`, `export.json` | Collected API summary and normalized planner input, only after successful collection |

Missing/zero/skipped/incomplete tests, missing coverage, stale evidence or unavailable tooling block acceptance. Nonzero process exits and failing TRX reports record `failed`; both `failed` and `blocked` exit 2. Failed tests stop before scanner end/upload. Exit 0 with `collected` means evidence collection completed, **not** that quality goals passed. Inspect `sonar.global_pass` in the retained evidence.

Logs and report bodies are redacted before hashes are recorded; these are retained sanitized reports, not byte-identical originals. Inspect before sharing: arbitrary sensitive output/filenames and oversized reports are not comprehensively sanitized. The real token stays in the Python HTTPS client; the scanner receives a short-lived loopback capability. Redirects and ambient HTTP proxies are refused. The loopback broker has not been tested against a real scanner and may require a future compatibility change.

## Feed one bounded job

After a successful authorized baseline, substitute its actual run path and returned task ID:

```powershell
rtk proxy python -B debtpack.py --state .debt-state/local-001.json plan 'C:\work\your-repository' '.debt-runs\BASELINE\export.json' --limit 1
rtk proxy python -B debtpack.py --state .debt-state/local-001.json context TASK_ID
rtk proxy python -B debtpack.py --state .debt-state/local-001.json transition TASK_ID running
# Host performs approved edits with failing-test-first verification, then collects a fresh AFTER run.
rtk proxy python -B debtpack.py --state .debt-state/local-001.json transition TASK_ID submitted
rtk proxy python -B debtpack.py --state .debt-state/local-001.json accept-local TASK_ID '.debt-runs\BASELINE' '.debt-runs\AFTER' --allow 'src/Changed.cs' --allow 'tests/ChangedTests.cs'
```

`accept-local` checks retained reports, source identity, chronological baseline/after runs, project/branch and 1-4 explicitly allowed changed files. It accepts disappearance of the selected non-security issue, independently of global quality. `accepted` is terminal for that job, distinct from offline `validated`; neither grants a parent review receipt. Acceptance errors exit 2 and leave the submitted state unchanged. Inspect the error and explicitly transition to `blocked` before continuing another job. At most two `running` attempts per job are allowed.

## Deliberate limitations

- No automatic agent/provider dispatch, fixes, commits, pipeline runs or hotspot disposition. Security jobs require human review and cannot be auto-accepted locally.
- Coverage/duplication metrics do not create file-level jobs. Issue disappearance alone does not prove unchanged behavior or exclude metric gaming; host review remains necessary.
- Snapshots include HEAD, diff and nonignored source file bytes, excluding build products. Ignored build inputs, external dependencies, environment, submodules and hostile concurrent mutation are not hermetically captured.
- Baseline and after evidence must both be fresh within 24 hours. Evidence is local and unsigned; hashes detect accidental changes, not forged envelopes by a malicious operator.
- API pagination, report sizes, taxonomy and file paths have conservative bounds. Missing/unsupported data blocks rather than silently implying zero debt.
- Existing baseline failures must be recorded and resolved under approved scope before claiming a passing baseline. Introduced failures must be fixed before handoff; do not skip tests or relabel failure as success.

See [validation](validation.md) for the separate offline-attestation format and [verification](verification.md) for actual fixture evidence.
