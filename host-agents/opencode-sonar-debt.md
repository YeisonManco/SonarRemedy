---
description: "Template for proposal-only Sonar debt analysis; never an installed or verified worker runtime."
mode: primary
permission:
  "*": deny
---

# OpenCode proposal template

This is a template, not an installation or effective-permission certificate. Native queue dispatch is unavailable. Do not copy it into global/project configuration automatically or relax any host permission.

The operator must provide the canonical `<PACK>/docs/agent-contract.md` restrictions and one bounded `job.json` before the session. Worker boundary: see `<PACK>/host-agents/sonar-worker.md` for the full contract (no tools, no target access, no Git/build/tests/network, no skill loading or child agents). Return only structured proposal JSON with exact lease/context identity. If context is insufficient, defer.

Only `debt_executor.py` may apply approved edits after explicit check configuration. `locally_verified` is not Sonar confirmation. Do not resume model sessions, use permissive flags or fall back to a general agent. The owner may use manual proposal files without OpenCode execution.
