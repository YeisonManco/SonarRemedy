"""Local subprocess fixture emulating Claude JSON. NEVER contacts a model/API."""

import json
import os
import sys
import time
from pathlib import Path

if "--version" in sys.argv:
    print("2.1.139 (Claude Code)")
    raise SystemExit(0)
if "--help" in sys.argv:
    print(
        "--bare ANTHROPIC_API_KEY --tools --disable-slash-commands --strict-mcp-config "
        "--mcp-config --no-session-persistence --output-format --model --session-id "
        "--max-budget-usd --no-chrome --system-prompt"
    )
    raise SystemExit(0)

started = time.time_ns()
request = json.loads(sys.stdin.read())
job = request["job"]
path = job["sources"][0]["path"]
session = sys.argv[sys.argv.index("--session-id") + 1]
if "contaminate" in path:
    (Path(job["binding"]["root"]) / "outside.cs").write_text("unexpected fixture SDK side effect\n")
if "timeout" in path:
    time.sleep(30)
if "junk" in path:
    print("zero-code non-JSON output")
    raise SystemExit(0)
delay = 0.15
if Path(path).stem.startswith("f") and Path(path).stem[1:].isdigit():
    delay = 0.05 * (3 - int(Path(path).stem[1:]) % 4)
time.sleep(delay)
proposal = {
    "version": 1,
    "job_id": job["job_id"],
    "attempt_id": job["attempt_id"],
    "lease": job["lease"],
    "context_fingerprint": job["context_fingerprint"],
    "status": "proposed",
    "reason": "",
    "risks": [],
    "test_plan": "Synthetic fixture; configured characterization checks remain external.",
    "edits": [
        {
            "path": path,
            "before_sha256": job["sources"][0]["sha256"],
            "replacements": [{"old": "Value()", "new": "Value( )"}],
        }
    ],
}
if "partial" in path:
    proposal["status"] = "partial"
if "wrong" in path:
    proposal["job_id"] = "wrong"
if "leak" in path:
    proposal["risks"] = [os.environ.get("ANTHROPIC_API_KEY", "")]
if "huge" in path:
    print("x" * (1024 * 1024 + 1))
    raise SystemExit(0)
if "deferred" in path:
    proposal.update(status="deferred", edits=[], reason="fixture_deferred")
print(
    json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": session,
            "num_turns": 1,
            "stop_reason": "end_turn",
            "result": json.dumps(proposal),
            "_fixture_start_ns": started,
            "_fixture_end_ns": time.time_ns(),
            "_fixture_sonar_present": "SONAR_TOKEN" in os.environ,
        }
    )
)
