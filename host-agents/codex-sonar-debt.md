# Codex manual proposal template

Template only; do not append/install configuration automatically. The canonical contract is `<PACK>/docs/agent-contract.md`; proposal output follows `<PACK>/host-agents/sonar-worker.md`.

Use only operator-supplied bounded context in a fresh, demonstrably tool-free session. No tools, target access, builds/tests, Git, network, skills or subagents. Return exact bound proposal JSON; defer missing context. A read-only sandbox alone still permits commands and is not a tool-free capability profile.

Native execution remains unavailable. Offline JSONL parser fixtures do not certify runtime permissions or authentication. Never bypass sandbox/approvals or ignore restrictive rules. Only the script integrator applies changes and verifies them; commits and Sonar confirmation are not worker operations.
