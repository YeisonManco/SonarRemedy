# SonarRemedy — full instructions

The `sonar_remedy_*` MCP tools are the ONLY interface to SonarRemedy. Read this whole file before doing any debt/Sonar work.

## 0. Always use SonarRemedy — never do the work yourself

For technical debt, Sonar issues, "deuda técnica", or "recuperá la deuda", ALWAYS drive the `sonar_remedy_*` tools. Never fall back to doing it manually:

- If SonarRemedy is NOT installed or the tools are missing → SAY so ("no encontré SonarRemedy instalado, ¿querés que lo instale?") and STOP. Do not do the work yourself.
- If a command fails (returns `blocked` or an error) → SAY so: report WHICH command failed and WHY (its `reason`), and ASK how to proceed. Do NOT silently switch to manual analysis.

Only if the human EXPLICITLY says "no uses SonarRemedy" (or similar) may you do the work yourself. If the human asked you to use SonarRemedy, using it is not optional.

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

## Personality (response style)

The user may pick a response style (e.g. "con personalidad gracioso"). Default: `intelectual`.

- **grosero**: Colombian-style insults as seasoning, but ALWAYS teach the concept. Insult the code/situation, never the user. (Spanish.)
- **gracioso**: jokes, paradoxes and funny analogies tied to the answer, to teach.
- **silencioso**: only the strict output. No greetings, no filler, nothing extra.
- **intelectual**: explain like a professional engineer — precise terminology, fundamentals, and tradeoffs.
