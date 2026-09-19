# checks.json reference (executor binding)

`configure --checks <file>` binds the reviewed check commands a queue's
integration may run. The validator (`debt_executor.validate_config`) is
deliberately strict: every rule below is enforced, and any violation raises a
`Blocked` naming the exact field (for example
`invalid_configured_check: checks[1].timeout_seconds ...`).

Start from `examples/debt-checks.example.json`. It is intentionally a
**template, not a runnable file**: `executable_sha256` holds a placeholder
because a hash must be computed from the real bytes on the integrating
machine — never invented, never copied.

## Top-level keys (exactly these 9)

| Key | Type | Rule |
| --- | --- | --- |
| `version` | int | Must be `1`. |
| `target` | string | Absolute path of the bound checkout (must equal the queue's bound root). |
| `branch` | string | Must equal the queue's bound branch. |
| `policy` | string | `red-first` or `characterization` (see below). |
| `characterization_reason` | string | ≤1000 chars. Required non-empty for `characterization`; must pair with an empty `expected_red`. |
| `test_paths` | list[str] | ≤2000 unique entries. Test edits are only allowed inside these paths. |
| `expected_red` | object | At most 100 `test-name → assertion-marker` pairs. Each marker must start with `Assert.`, `AssertionError:` or `Expected`. Empty unless `policy` is `red-first`. |
| `allowed_outputs` | list[str] | Generated files the fix may add. Each must live under `bin/`, `obj/` or `artifacts/` and end in `.dll`, `.pdb`, `.cache`, `.deps.json` or `.runtimeconfig.json`. |
| `checks` | list | 2–8 entries (see below). |

## Checks entries

Each entry has exactly these keys (`report` only for `trx`):

| Key | Rule |
| --- | --- |
| `name` | Token `[A-Za-z0-9_.-]{1,128}`, unique (case-insensitive). |
| `kind` | `build` or `trx`. The set must be exactly one `build` plus `trx` entries, and `checks[0]` must be the `build`. |
| `argv` | 1–64 strings, each ≤8192 chars, no NUL. `argv[0]` must be an **absolute** `.exe` path that is not `git`/`ssh`/`sonar-scanner`. No credential arguments (`sonar.token`, `--password`, `--token`, `authorization:`). `{run}` is the only placeholder: it is replaced with a unique evidence directory outside the target. |
| `executable_sha256` | SHA-256 of the exact `argv[0]` bytes on the integrating machine. Compute it with: `python -B -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <tool.exe>` — recompute after every tool update, or integration blocks with `check_executable_hash_mismatch`. |
| `cwd` | `"."` (repo root) or a repo-relative directory that must exist. |
| `timeout_seconds` | int 1..1800. |
| `report` (`trx` only) | Bare filename ending in `.trx` (no `/`). |

## Policies

- `red-first`: needs non-empty `test_paths` and non-empty `expected_red`, plus exactly one `trx` check. Test edits run first and must reproduce the expected failure before the fix.
- `characterization`: needs a non-empty `characterization_reason` and an empty `expected_red`. For behavior-preserving refactors without a failing test.

## binding flow (human-owned)

1. Author the file from the example (real paths, real sha256, real failing-test markers).
2. Dry-run: `sonarremedy configure --state <queue> --repo <path> --checks <file>` — validates the shape and prints `checks_sha256` without writing anything.
3. Review the dry-run output yourself, then approve explicitly: repeat with `--approve-checks-sha256 <digest> --execute`.
4. Only then may `integrate` run, and only the bound checks run — nothing else executes on the target.
