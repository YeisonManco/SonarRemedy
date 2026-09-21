# VSCode / Copilot manual proposal template

This template is guidance only, not an installed Chat extension or headless transport. Review `<PACK>/docs/agent-contract.md` and `<PACK>/docs/work-queue.md`. The `examples/vscode-queue.tasks.json` template invokes queue preview/monitor commands in a terminal; it is not installed automatically.

For optional Chat assistance, the operator supplies the proposal-only worker contract and one bounded job context to a fresh non-agent session. Worker boundary: see `<PACK>/host-agents/sonar-worker.md` for the full contract (**no tools**, no target access, no Git/build/tests/network, no skill loading or child agents). If the host cannot enforce this boundary, author the proposal JSON manually rather than using a general-agent fallback.

Save the returned strict proposal at the queue's manual inbox path. Only the separately authorized script integrator writes the worktree and runs exact checks. Copilot Chat is not equivalent to Copilot CLI; native CLI dispatch remains unavailable without permissive flags or auth changes. Local verification is not Sonar confirmation.
