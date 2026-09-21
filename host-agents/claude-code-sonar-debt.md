# Claude Code manual proposal template

Template only: this Markdown does not register an agent or establish native restrictions. Native queue dispatch and its terminal/tool-trace parser remain unavailable. Do not install it or change authentication/permission settings to force execution.

The operator supplies `<PACK>/docs/agent-contract.md` restrictions and one bounded job context to a demonstrably tool-free fresh session. Worker boundary: see `<PACK>/host-agents/sonar-worker.md` for the full contract (no tools, no target edits, no builds/tests/Git/network, no skill loading or child agents). Return a strict proposal, never claimed execution evidence.

Do not assume `--bare` reuses the owner's existing authentication mode; do not switch auth, enable bypass modes or reuse ambient sessions. Manual proposal JSON works independently of Claude. Only the configured script integrator performs target effects; local checks never imply Sonar confirmation.
