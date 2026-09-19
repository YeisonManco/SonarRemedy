# SonarRemedy

Recover SonarQube technical debt automatically — a **proposal-only worker swarm with a serial integrator**, driven by any AI (VS Code Copilot, OpenCode, Claude Code) over MCP.

## Install

Python 3.11+ (stdlib only — no pip dependencies). Windows is the primary target.

**A. Install as a CLI (recommended):**

```powershell
git clone https://github.com/YeisonManco/SonarRemedy
cd SonarRemedy
python -m pip install -e .
```

> **Use the editable install (`-e`).** `sonarremedy update` auto-detects the clone
> via the editable install; a non-editable install moves the code to `site-packages`
> and `update` cannot find the clone anymore.

This installs three commands:

| Command | What it runs |
|---|---|
| `sonarremedy` | the facade CLI (`fetch`, `slice`, `run`, `status`, `init`, …) |
| `sonar-remedy-config` | the interactive config wizard |
| `sonar-remedy-mcp` | the MCP server for the editor |

**B. Run without installing** (skip `pip install`, call the module directly):

```powershell
python -B SonarRemedy/sonar_remedy.py --help
```

> **Important:** this installs the CLI for the **terminal only**. Copilot Chat in VS Code does **not** know about SonarRemedy until you run `sonarremedy init` in your project — see [Set up an editor](#set-up-an-editor-mcp). Install and editor setup are **two separate steps**.

## Update

Update the pack with one command — it works from anywhere (the clone is auto-detected):

```powershell
sonarremedy update
```

It runs `git pull` + `pip install` for you. If you need to point at a specific clone, use `sonarremedy update --path C:\path\to\SonarRemedy`. Check the version with `sonarremedy --version`.

## What it does

1. **Fetch** — pulls open issues, measures, and the quality gate from Sonar (chunked when the project exceeds the issue budget).
2. **Queue** — a durable SQLite queue groups findings by file+kind and leases bounded proposal contexts.
3. **Propose** — a proposal-only worker (a sub-agent or headless model) reads one bounded `job.json` and returns a strict `proposal.json`. Workers never touch the repo.
4. **Integrate** — a single serial integrator applies proposals, runs configured checks (RED/GREEN), and verifies byte-for-byte.
5. **Track** — `status`/`progress` report the next action, remaining jobs, and an ETA.

## How it works

```mermaid
flowchart TD
    U["User: recover the debt of X"] --> O[Orchestrator<br/>AI drives the CLI]
    O --> F[fetch<br/>Sonar issues to export.json]
    F --> S[slice<br/>build durable queue]
    S --> R[run --execute<br/>lease N jobs, one job.json each]
    R --> W1[Worker 1<br/>proposal-only]
    R --> W2[Worker 2<br/>proposal-only]
    R --> WN[Worker N<br/>proposal-only]
    W1 --> I[Serial integrator<br/>apply, RED, GREEN, verify]
    W2 --> I
    WN --> I
    I --> ST[status<br/>next action + ETA]
    ST -->|re-scan| F
```

**Concurrency model:**

- **One worktree per branch** — set up once by the orchestrator, never one per worker.
- Workers **propose in parallel** — each is isolated (no tools, no repo access) and reads only its own `job.json`.
- The integrator **applies serially** — one proposal at a time (RED → GREEN → byte-for-byte), even though proposals were prepared in parallel.
- **No commits/push in the pack** — the integrator edits the files; committing and pushing are separate human operations.
- Each **branch** gets its own queue + worktree + evidence; fixes never cross branches.

## Architecture

**Python does all the mechanical analysis; the AI does exactly one thing — propose the fix.**

| Layer | Modules | Role |
|---|---|---|
| Fetch | `sonar_fetch.py`, `sonar_client.py` | Pull Sonar issues (chunked if over budget) |
| Queue | `debt_queue.py` | Durable SQLite queue — claims, leases, fail-closed binding |
| Detect | `sonar_suppressions.py`, `sonar_exclusions.py`, `language_for`/`hint_for` | Find suppressions; detect language + distilled hints |
| Integrate | `debt_executor.py`, `debt_runner.py` | Apply proposals serially, run checks, verify byte-for-byte |
| Report | `status`/`progress`/`schedule` | Next action, ETA, parallel/serial plan |
| **AI (external)** | proposal worker | Reads one `job.json` → returns one `proposal.json` |

Everything that does not need AI is mechanical (zero tokens). The AI only proposes
what the mechanical tools cannot: understanding the code and writing an idiomatic fix.

## Commands

`sonarremedy <command> [flags]`

| Command | What it does |
|---|---|
| `fetch` | Pull Sonar issues into an export (chunked if over budget) |
| `slice` | Build a durable queue from an export |
| `run` | Lease/process a bounded manual proposal batch |
| `integrate` | Serially apply a recorded proposal + run bound checks |
| `configure` | Bind reviewed check commands + the target snapshot |
| `scan-suppressions` | Detect Sonar-evasion directives (`certain` vs `ambiguous`) |
| `scan-exclusions` | Detect Sonar exclusions/suppressions by language + category (17 rules) |
| `rules` | Manage the exclusion whitelist/blacklist (`list`/`allow`/`block`/`remove`) |
| `report` | List applied fixes + the human follow-up each requires |
| `status` | Report the queue's next action + ETA |
| `progress` | Write a human-readable progress file |
| `schedule` | Show the parallel/serial plan for pending jobs |
| `analyze` | Run the local pipeline to regenerate Sonar results |
| `run-all` | Fetch + slice every chunk into its own queue |
| `projects` | List saved project configs |
| `configure-project` | Save a project config non-interactively |
| `configure-projects` | Interactively register multiple projects |
| `init` | Wire up VS Code + Copilot, and create `.sonarremedy/` (idempotent) |
| `clean` | Remove generated state + sibling worktrees (keep `rules.json`) |
| `reset` | Remove `.sonarremedy/` entirely + sibling worktrees (back to zero) |
| `check` | Report whether the project's setup is up to date with the installed pack |
| `doctor` | Diagnose the setup + a queue's identity (root/branch/revision) with fixes |
| `update` | `git pull` + reinstall the pack from its clone |

## Diagnose

`doctor` reports what is wrong and how to fix it — use it before guessing:

```powershell
sonarremedy doctor                                 # project version + setup
sonarremedy doctor --state <queue> --repo <path>   # compare the queue's identity
sonarremedy doctor --fix                            # apply the safe repairs
```

- `--state` is the **queue directory** created by `slice` (it contains `queue.sqlite3`), not an export `.json` file.
- Each identity mismatch reports the **exact fix command**: `git -C <root> checkout <branch>` (branch), `run --repo <bound root>` (root), or re-fetch + re-slice (revision).
- `--fix` applies only the **safe repairs** — it re-runs `init` when the project's recorded version is behind the pack or init files are missing (and re-writes the pointer + version marker). It **never** touches a queue or the git state.

> **One worktree per branch.** Slicing binds the queue to the checkout you passed. On a multi-worktree repo, always use the SAME `--repo` path across `fetch` → `slice` → `run`, or you will hit `target_identity_mismatch`.
>
> **Init per worktree.** A new worktree comes from `HEAD`, so it does NOT inherit the uncommitted `.github/` instructions, `.vscode/mcp.json`, or the gitignored `.sonarremedy/` setup — and you do NOT need to commit them. After `git worktree add`, run `sonarremedy init --dir <worktree>` (idempotent, merges your instructions) and verify with `sonarremedy doctor --dir <worktree>`; if files are missing, `doctor --fix --dir <worktree>` recreates them.

## Quick start

```powershell
# 1. Configure a project (Sonar URL, project key, token, repo, branch)
sonar-remedy-config --project <name> --persist

# 2. Fetch Sonar issues (chunked if over the budget)
sonarremedy --project <name> fetch --repo <path>

# 3. Slice the queue, run a batch, then resume
sonarremedy slice --export <export.json> --state <queue> --execute
sonarremedy run --state <queue> --execute --limit 8
# ... workers propose fixes ...
sonarremedy run --state <queue> --resume --execute
sonarremedy status --state <queue>
```

If you skipped the install, replace `sonarremedy` with `python -B SonarRemedy/sonar_remedy.py` and `sonar-remedy-config` with `python -B SonarRemedy/sonar_remedy_config.py`.

## Talk to the AI (after install + init)

Paste these in Copilot Chat, in order. Close Visual Studio before integrating (locked files fail the build gate).

1. *"Recuperá la deuda del proyecto <name> en la rama <branch>: fetch y después slice a un state nuevo `state-<branch>`. Explicame qué trajiste."*
2. *"Corre el run con execute y avisame cuando estén las propuestas."*
3. *(it writes/resumes proposals)* — if it asks for checks: *"Corre `sonar_remedy_detect_checks` con el repo, presentame borrador + faltantes y esperá."*
4. Complete the `[HUMAN]` fields (exact bound branch, policy/reason or real RED markers), save as `checks.json`, then: *"Corre el configure dry-run y mostrame el sha."*
5. Verify the sha, then: *"Aprobado con ese sha. Ejecutá el configure e integrá con autopilot `--integrate`."*

Standing rules (also wired into the Copilot instructions by `init`): never do the work manually, never commit/push with `--no-verify`, and on any `blocked` report the exact reason and stop — a failed baseline build keeps the job `proposed` for retry, it never applies anything red.

## Deterministic flow (autopilot)

Copilot interprets instructions, so it can improvise. `autopilot` is the harness that owns the flow instead: every call advances as far as the rules allow — setup-gate, slice, run, configure-gate, integrate, status — and reports its phase with the exact next command. The model only fills bounded proposals; it never chooses a step.

```powershell
sonarremedy autopilot --state <queue> --repo <path>             # plan only (dry-run)
sonarremedy autopilot --state <queue> --repo <path> --export <export.json> --execute
# ... write one proposal per waiting proposal_path ...
sonarremedy autopilot --state <queue> --repo <path> --execute   # resume
sonarremedy autopilot --state <queue> --repo <path> --execute --integrate
```

- Dry-run is the default: it prints the ordered phases and writes nothing.
- A repo without `init` stops at the setup gate with the exact `init --dir` fix (no commit/push of instructions required — see the worktree note above).
- Proposals stop at `awaiting_proposals` with each `proposal_path`; integrating stops at the configure gate until checks are explicitly bound (`configure --checks ... --approve-checks-sha256 ... --execute`).
- The same flow is available to Copilot as the `sonar_remedy_autopilot` MCP tool.

## Authoring checks.json (executor binding)

Integration runs **only** the check commands you explicitly bind — nothing else executes on the target. Write them from `examples/debt-checks.example.json` (a template: the sha256 placeholder must be replaced, never invented) following `docs/checks-reference.md`:

1. Real absolute `.exe` paths, real `cwd`, real failing-test markers; compute each sha256 with `python -B -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <tool.exe>` (recompute after every tool update).
2. Dry-run first: `sonarremedy configure --state <queue> --repo <path> --checks <file>` — validates the shape and prints `checks_sha256` without writing.
3. Review it yourself, then approve: repeat with `--approve-checks-sha256 <digest> --execute`.

Don't hand-write it from scratch: `sonarremedy detect-checks --repo <path>` drafts it from the repo (solution, test projects, `dotnet` + real sha256) and tells you the 1–2 judgments to complete (same flow as `sonar_remedy_detect_checks`).

## Commit gate (pre-push hook)

`init` installs a pre-push hook into the project's `.git/hooks`, so broken code cannot be pushed: the hook runs the project's gates and blocks the push on red. Never use `--no-verify`.

- Pack repos: suite + `ruff check` + `ruff format --check` (mirrors CI).
- Managed projects: commands declared in `.sonarremedy-hooks.json` (`{"pre-push": [[...argv...]]}`); without it, the hook blocks with the exact shape to declare.
- `doctor` verifies the hook (`git_hooks`: ok/missing/foreign/outdated) and `--fix` reinstalls it. A foreign hook is never overwritten.
- Enforcement travels with the pack: hook + harness + instructions need no server settings. Optionally, on repos you own, add branch protection (GitHub → Settings → Branches → rule for `main`: require status checks) as a server-side backstop.

To save a project config non-interactively (for scripts), use:

```powershell
sonarremedy configure-project --name <name> --sonar-url <url> --project-key <key> `
  --repo-url <git-url> --local-path <path> --worktree-root <path>
```

The config stores the **name** of the token env var, never the value. Each project can use its own var, so multiple Sonar servers/accounts coexist:

```powershell
sonarremedy configure-project ... --token-env SONAR_TOKEN_PROJECT_B --pat-env GIT_PAT_PROJECT_B
```

## Set up an editor (MCP)

One command wires up VS Code + Copilot Chat:

```powershell
sonarremedy init --dir <your-project>
```

It writes:

- `.vscode/mcp.json` — the `sonar-remedy` MCP server (points at this pack).
- `.github/copilot-instructions.md` — the Copilot instruction.

Reload VS Code, then in Copilot Chat: *"recuperá la deuda de `<proyecto>`"*.

**Verify it's wired up** (optional): confirm the two files exist, then test the server in a terminal:

```powershell
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | sonar-remedy-mcp
```

It should reply with `"serverInfo":{"name":"sonar-remedy"...}`. In VS Code, the `sonar_remedy_*` tools then appear in Copilot Chat (you may need to reload the window: Ctrl+Shift+P → "Developer: Reload Window").

**Manual** (the same two files, by hand):

1. Copy `host-agents/vscode-mcp.json` to the project's `.vscode/mcp.json` (adjust the `args` path).
2. Copy `host-agents/copilot-instructions.md` to `.github/copilot-instructions.md`.

The MCP server exposes every command as a tool: `sonar_remedy_fetch`, `sonar_remedy_slice`, `sonar_remedy_run`, `sonar_remedy_status`, `sonar_remedy_progress`, `sonar_remedy_schedule`, `sonar_remedy_analyze`, `sonar_remedy_run_all`, `sonar_remedy_configure_project`, `sonar_remedy_scan_suppressions`, `sonar_remedy_scan_exclusions`, `sonar_remedy_rules`, `sonar_remedy_report`, …

## Safety boundaries

- Workers are **proposal-only** — no tools, no repo access. Only the serial integrator writes.
- Binding **fails closed**: wrong target/branch/revision blocks, never silent.
- Secrets (`SONAR_TOKEN`, `GIT_PAT`) live in the environment — never in files, args, or prompts.
- Mutations require `--execute`; dry-run is the default.
- `locally_verified` (tests passed) is not `sonar_confirmed` (Sonar no longer reports it); a fresh re-scan confirms.
- A **suppression scan** (`scan-suppressions`) flags directives that may evade Sonar (`NOSONAR`, `#pragma`, `# noqa`, …) as `certain` or `ambiguous` — only the `ambiguous` ones need AI judgment, so no tokens are spent on clear cases.
- An **exclusions scan** (`scan-exclusions`) reports Sonar exclusions/suppressions/coverage exclusions/technical exceptions by language + category, each with a severity (danger level). It is a **separate, optional** scan for "just the exclusions". Exclusions are **never auto-fixed** — a suppression may be legitimate, so a human reviews each one.

## Development

See `CONTRIBUTING.md` — TDD (RED/GREEN), Python stdlib only (no pip dependencies), and keep the MCP server in sync with the CLI. The full queue contract is in `docs/work-queue.md`.

Per-language fix skills live in `skills/` — `dotnet-index.md`, `python-index.md`, `angular-index.md`, `react-index.md`, plus the Sonar workflow skills under `skills/sonar/`. A worker fixes using both the kind hint and the language hint.

## Tests

```powershell
python -B -m unittest discover -s tests -v
```

## License

MIT — see `LICENSE`.
