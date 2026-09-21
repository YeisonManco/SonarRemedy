# SonarRemedy orchestrator (briefing → pipeline)

> **Read this instruction FIRST**, before exploring any repository. The pack's
> rules take precedence over the target repo's rules. **This pack is READ-ONLY:
> never create/edit/delete any file under it** — you only run its CLI and read
> its rules/config. Never explore the target broadly — run only the pack CLI
> and read the bounded `job.json` contexts it produces.

Template for the AI that DRIVES debt recovery from a natural-language request.
This is the orchestrator, NOT the proposal worker: the orchestrator runs the
pack CLI; workers only return proposals (see `sonar-worker.md`).

## Ground rules

1. **The pack is read-only.** Modifying `sonar_remedy.py`, `debt_*`, `sonar_*`,
   or any other file here is never part of the task. Run the CLI, don't edit it.
2. **Work automatically.** Default everything from the persisted config
   (`~/.sonar-remedy/projects/<name>.json`). Ask only what the config lacks, or at a
   real product/risk decision: hotspot disposition, branch propagation, binding
   checks, or `status` → `re_scan_required`.
3. **Never read the export or the target in bulk.** Read compact command output
   and one bounded `job.json` at a time.

## Entry

When the user says "recuperá la deuda del proyecto X" (or similar):

0. **Resolve the checkout identity first, and pass it explicitly every time.**
   The pack binds queues to the EXACT git identity (root/branch/revision) of the
   checkout you pass, so a recovery driven from the wrong folder runs against the
   wrong project. Before anything else: `git rev-parse --show-toplevel` and
   `git rev-parse --abbrev-ref HEAD` for the repo you are actually working in, and
   pass that exact path as `--repo`/`--dir` on every command. `doctor --dir <checkout>`
   (or `--repo`) now reports a `project` check naming which saved project binds to
   that checkout (`ok` / `ambiguous` / `warning`); run it before mutating anything.
1. **Briefing — ask only what is missing.**
   - Local checkout path (`repository.local_path` in config, or ask).
   - Main branch (default `main`).
   - Worktree root (config or ask).
   - Sonar URL / project come from the config; the token is ALWAYS from the
     environment (`SONAR_TOKEN`), never from the prompt, args or a file.

2. **Drive the pipeline** (run from the pack directory, `python -B`):
   - `sonar_remedy.py fetch --repo <path>` → `export.json` (or `chunk-0.json`,
     `chunk-1.json`, … when the project exceeds the issue budget — see `result.exports`).
   - For each export in `result.exports`, run `slice` + `run` + `resume` in
     sequence before moving to the next chunk (chunks are independent queues).
   - `sonar_remedy.py slice --repo <path> --export <export.json> --state <q> --execute`
   - `sonar_remedy.py run --state <q> --execute --limit N` → `awaiting_proposals`
     (each job has `context_path` + `proposal_path`)

3. **Produce proposals (worker step)**: for each leased job, produce a proposal
   from the bounded `job.json` — either by reasoning yourself, or by delegating to
   a fresh proposal-only worker sub-agent (a separate, possibly cheaper model) that
   reads ONLY the `job.json` and returns `proposal.json`. Include BOTH distilled
   fix hints: the job kind's (`sonar_remedy.hint_for(kind)`) AND the file language's
   (`sonar_remedy.language_hint_for(path)`). For more depth, READ the per-language
   skill (`skills/<language>-index.md`, e.g. `dotnet-index.md`, `python-index.md`,
   `angular-index.md`, `react-index.md`) and the Sonar fix skill
   (`skills/sonar/sonar-fix-issue/SKILL.md`) yourself, then inject their DISTILLED
   guidance into the worker prompt — never dump the full skills. Write each proposal
   to its inbox `proposal_path`. The worker never touches the target repo.
    Proposal v1 shape: `status` "proposed", `edits=[{path, before_sha256,
    replacements:[{old,new}]}]`, `reason` matches `^[A-Za-z0-9_.-]{1,128}$`
    (no spaces), plus `risks`, `test_plan`, and `follow_up` (a list of
    `{action, name, note}` for human actions the fix requires, `[]` when none).

4. **Consume and report**: `sonar_remedy.py run --state <q> --resume --execute`,
   then `sonar_remedy.py status --state <q>` for the next action.

5. **Integrate (only with bound checks)**: `sonar_remedy.py configure --checks
   <checks.json> --approve-checks-sha256 <digest> --execute` then
   `sonar_remedy.py integrate --state <q> --job <id> --execute`.

6. **When the queue is exhausted** (`status` returns `next_action:
   re_scan_required`), generate fresh Sonar results locally:
   `sonar_remedy.py analyze --script <pipeline.ps1> --repo <path> --execute`
   (token from env), then `fetch` + `slice` and continue. If no local pipeline
   is configured, tell the user to commit+push, re-run their Sonar pipeline,
   `git pull`, then re-fetch. Do NOT re-fetch without a new analysis.

## Rules

- Secrets live in env only (`SONAR_TOKEN`, `GIT_PAT`); never in config, prompt,
  args or files. The config stores environment NAMES only.
- `fetch` is HTTPS-only; an HTTP on-prem Sonar needs TLS/proxy or a manual export.
- No commits/push; no suppressions/exclusions to game metrics.
- A proposal is never applied by the worker; only the serial integrator edits.
