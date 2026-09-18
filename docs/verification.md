# Fixture verification, awaiting parent review

## Resumed local-scan increment (2026-09-16)

- Scope: only this pack. No real Sonar calls, sibling builds, commits, push or subagents. Codebase-memory was queried first; its index did not include the new Sonar symbols, so narrow direct reads were used.
- Baseline: **50 tests passed**, 0 failures/errors/skips. Raw output: `.debt-state/resume-verification/baseline.txt`.
- Regression RED: 17 local tests ran with **3 failure entries** (counter mismatch and two missing-hash subcases). Output: `.debt-state/resume-verification/red.txt`.
- GREEN: **53 tests passed**, 0 failures/errors/skips. Output: `.debt-state/resume-verification/green.txt`. Discovery uses `python -B -m unittest discover -s tests -v`; the retained log was captured by the equivalent `unittest.defaultTestLoader.discover('tests')` and `TextTestRunner(verbosity=2)` with exit status tied to `wasSuccessful()`.
- Corrected `sonar_local.verify_run` to require hashes for retained TRX/OpenCover and reparse counters before acceptance. Added selected-job scope/global-result fixture coverage.
- Existing real local process tests exercise timeout termination, redaction, CLI dry-run and rejection of skip switches. Loopback HTTP uses a fake upstream; scanner, Git and remote API evidence use fixtures/mocks. No .NET build/scanner was launched.
- Completed README/host/validation guidance and added `docs/local-scan.md` plus `examples/sonar.local.example.json`. Existing `debtpack.py accept-local` integrates baseline/after evidence with planner state; no new dispatch mechanism was added.

**Unfinished:** live scanner/broker/server compatibility, genuine target test/coverage/Sonar results, independent security review and parent review receipt. No functionality guarantee, token-saving measurement or real quality approval is claimed. Read the local scan guide before authorizing an actual run.

Rollback boundary for this resumed work: the report-verification addition in `sonar_local.py`, three added tests in `tests/test_sonar_local.py`, the new local-scan guide/config example and corresponding documentation updates. Keep the pre-existing interrupted implementation and operator-owned evidence. The complete local-scan feature spans `sonar_local.py`, `sonar_client.py`, `sonar_gateway.py`, existing `debtpack.py` integration and their tests; do not partially remove that feature by deleting only its imports.

## Historical offline-only slice

The following records describe the earlier 17-test delivery, not current capabilities or inventory. The resumed evidence above supersedes the old counts and not-implemented list.

## Evidence

- Initial inspection: `D:\repos\MVM\Fronteras\Agentes` existed and was empty, including hidden entries. No interrupted files or unrelated content were overwritten.
- Loaded exact installed standards: `C:\Users\jeiss\.agents\skills\cognitive-doc-design\SKILL.md` and `C:\Users\jeiss\.agents\skills\work-unit-commits\SKILL.md`. `skill_resolution: paths-injected` (directly loaded by sole writer, no delegation).
- RED 1: test suite written before implementation; discovery failed with `ModuleNotFoundError: debtpack` (one loader error). GREEN 1: 11 tests passed.
- RED 2: local-report requirements added as two tests; 13 tests ran with 2 errors for the missing `evidence_root` argument. GREEN 2: implementation plus CLI smoke suite passed.
- Final command, from pack root: `rtk proxy python -B -m unittest discover -s tests -v` — **17 tests passed, 0 failures, 0 errors, 0 skipped** on Python 3.11/Windows.
- Four real-process CLI smoke tests cover discovery/planning/context/state/submission/blocked validation, synthetic accepted evidence with existing report files, no overwrite and escaped state rejection. All fixtures are temporary directories inside the pack and are removed after tests. Target file hashes remain unchanged in the end-to-end blocked scenario.
- Unit tests cover traversal, secret exclusion with content reads prohibited, deterministic bounds, malformed input, duplicate IDs, stripped message content, state transitions, single active job, missing reports, report hashes, strict metric boundaries, revision mismatch, stale/future timestamps, NaN and symlinks. The symlink test ran, not skipped.
- `rtk proxy gentle-ai --help` identified version 3.0.1. `rtk proxy gentle-ai review start --help` returned its read-only help successfully. The native CLI surface is available; an actual review was **not** started. No receipt exists or is claimed.
- No host autodiscovery claim is made. Bridges use explicit file reads/paste fallback, not unverified configuration placement advice.

These tests verify pack behavior against synthetic data. They do **not** establish target coverage, build/test health, Sonar quality, security approval or source-report authenticity. No actual target repository or Sonar account was used. Pack code coverage itself was not measured.

## Inventory

All paths below are relative to this folder; no sibling repository was written.

| Paths | Purpose |
|---|---|
| `debtpack.py` | Standard-library discovery, planning, bounded context, local state and validation CLI |
| `tests/test_debtpack.py`, `tests/test_cli.py` | 13 unit tests and 4 CLI smoke tests |
| `README.md`, `AGENTS.md` | Portable quickstart and explicit host workflow |
| `prompts/coverage.md`, `prompts/smells.md`, `prompts/duplication.md`, `prompts/security.md` | Specialist contracts, not autonomous agents |
| `bridges/copilot.md`, `bridges/opencode.md`, `bridges/codex.md` | Merge-only explicit-reading instructions |
| `examples/config.json`, `examples/sonar-export.json`, `examples/evidence.json` | Reference configuration and deliberately nonvalidated example input |
| `docs/validation.md`, `docs/evidence.schema.json`, `docs/verification.md` | Evidence contract, schema and this report |

18 authored files. `.debt-state/` may remain empty after tests; it is the only CLI state-write boundary.

## Reviewable work units and rollback

1. Offline workflow: `debtpack.py`, both test files, README, AGENTS, examples and validation/schema docs form a cohesive behavior slice. Roll back together; retain any operator-owned `.debt-state` records separately. Verification is the 17-test command above, including runtime smoke evidence.
2. Host contracts: `prompts/` and `bridges/` are removable without changing the CLI. They require human host execution, so real provider-runtime verification is N/A: provider runners are not implemented. Do not advertise context usability without these contracts.

This delivery exceeds 400 authored lines including tests/docs. No code was compressed to fit a review budget. Parent should retain the full snapshot and apply its review/size policy; no commits, Git init or push occurred. Parent review is pending, not approved.

## Outstanding limitations

Live Sonar access/scanners, automatic dispatch, subprocess execution, raw API normalization, report parsing/authentication, revision/dirty-tree lookup, configuration loading and concurrency locks are **NOT IMPLEMENTED**. Local validation is operator-attested, timestamp-bounded and revision-string-bound only. Historical status must not be reused after target changes. Discovery uses conservative filename heuristics and may miss custom tooling; suggested commands need manual verification. No credentials or remote writes are needed.
