<!-- Template — copy this file to `.github/copilot-instructions.md` in the target repo. -->

# SonarRemedy

For technical debt, Sonar issues, "deuda técnica", "recuperá la deuda", exclusions, or suppressions, you MUST FIRST read and follow the full instructions in `.github/sonarremedy-instructions.md` (in this repo) — including the exact `proposal.json` contract (section 12). Do not rely on this short pointer alone.

**Core rule:** ALWAYS use the SonarRemedy MCP tools (`sonar_remedy_*`). If SonarRemedy is not installed or a command fails, REPORT it clearly (what failed + why) and ASK the human — never do the work manually unless the human explicitly says "don't use SonarRemedy".

**Commit rule:** NEVER commit or push with `--no-verify` (or any hook bypass). The pre-push hook installed by `init` is the build/suite gate: a red gate blocks the push until the failure is fixed — fix the code, never silence the gate. If the hook is missing or outdated, run `sonarremedy doctor --dir <project>` (and `--fix`) instead of pushing blind.

**Document-before-commit rule:** every user-visible change ships its documentation in the SAME commit — no commit or push without writing down what changed, why, and any recovery you applied. For the pack: `CHANGELOG.md` (and `README.md` for commands/install/flow changes) plus the Troubleshooting FAQ for any new blocked reason. For a target repo: record the change and its recovery where that repo documents decisions. Docs are not optional or a follow-up — they go in with the fix.
