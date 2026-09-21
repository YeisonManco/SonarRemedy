# Codex manual proposal template

Template only; do not append/install configuration automatically. The canonical contract is `<PACK>/docs/agent-contract.md`. Worker boundary: see `<PACK>/host-agents/sonar-worker.md` for the full contract (no tools, no target access, no builds/tests/Git/network, no skills or subagents).

Use only operator-supplied bounded context in a fresh, demonstrably tool-free session. Return exact bound proposal JSON; defer missing context. A read-only sandbox alone still permits commands and is not a tool-free capability profile.

Native execution remains unavailable. Offline JSONL parser fixtures do not certify runtime permissions or authentication. Never bypass sandbox/approvals or ignore restrictive rules. Only the script integrator applies changes and verifies them; commits and Sonar confirmation are not worker operations.
