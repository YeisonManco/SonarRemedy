"""Bounded proposal queue, explicit Claude bare profile, and serial integrator."""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import debt_queue as queue

DEPRECATION_WARNING = (
    "WARNING: debt_work.py's direct CLI is deprecated. It bypasses sonar_remedy.py's"
    " safety checks (project auto-detection, doctor, queue registration) and will be"
    " removed in a future release. Use `sonarremedy` instead."
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument(
        "--state", required=True, help="queue directory outside the target; never overwritten"
    )
    root.add_argument("--expect-target", help="optional assertion of the existing queue target")
    root.add_argument("--expect-branch", help="optional assertion of the existing queue branch")
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser("slice", help="preview or create the complete offline queue")
    create.add_argument("--target", required=True)
    create.add_argument("--export", required=True)
    create.add_argument("--branch", required=True)
    create.add_argument(
        "--write-sets", help="JSON map of primary paths to explicitly approved full write sets"
    )
    pick = commands.add_parser(
        "next", help="read-only next eligible stage; batch size counts files"
    )
    pick.add_argument("--limit", type=int, default=4)
    claim = commands.add_parser(
        "claim", help="lease one bounded proposal context; no worker is launched"
    )
    claim.add_argument("--job")
    claim.add_argument("--lease-seconds", type=int, default=1200)
    complete = commands.add_parser(
        "complete", help="record a proposal, deferred result or failure; never a verified fix"
    )
    complete.add_argument("--proposal", required=True)
    defer = commands.add_parser(
        "defer", help="defer pending/proposed work without changing target files"
    )
    defer.add_argument("--job", required=True)
    defer.add_argument("--reason", required=True, help="short non-secret reason code")
    reconcile = commands.add_parser(
        "reconcile", help="resolve an expired lease, never automatically retry"
    )
    reconcile.add_argument(
        "--receipt", required=True, help="saved claim JSON containing exact lease identity"
    )
    reconcile.add_argument("--effects", required=True, choices=["none", "unknown"])
    commands.add_parser("monitor", help="read-only compact accounting; no source or raw messages")
    watch = commands.add_parser("watch", help="bounded read-only compact progress stream")
    watch.add_argument("--interval", type=float, default=2)
    watch.add_argument("--duration", type=float, default=60)
    document = commands.add_parser(
        "document", help="regenerate deterministic progress.json and report.json"
    )
    configure = commands.add_parser(
        "configure", help="bind human-reviewed exact check commands and current target snapshot"
    )
    configure.add_argument("--checks", required=True)
    configure.add_argument("--approve-checks-sha256")
    integrate = commands.add_parser(
        "integrate", help="serially apply a recorded proposal and configured behavioral checks"
    )
    integrate.add_argument("--job", required=True)
    run = commands.add_parser(
        "run", help="bounded manual or explicitly configured Claude native batches"
    )
    run.add_argument(
        "--provider", choices=["manual", "opencode", "codex", "claude", "copilot"], default="manual"
    )
    run.add_argument(
        "--resume", action="store_true", help="resume queue state, never a model session"
    )
    run.add_argument(
        "--integrate",
        action="store_true",
        help="also authorize serial target edits/checks using bound configuration",
    )
    run.add_argument("--limit", type=int, default=4)
    run.add_argument("--max-batches", type=int, default=10)
    run.add_argument("--wall-seconds", type=int, default=3600)
    run.add_argument("--inbox")
    run.add_argument(
        "--profile",
        help="explicit provider profile JSON; no credential values or inferred authentication",
    )
    for command in (create, claim, complete, defer, reconcile, document, configure, integrate, run):
        command.add_argument(
            "--execute",
            action="store_true",
            help="explicitly authorize this operation; default is no-write/no-process preview",
        )
    return root


def load(path: str, limit: int) -> Any:
    return queue.parse_json(queue.read_bytes(path, limit))


def main(
    argv: list[str] | None = None,
    *,
    identity_reader: Callable[[Path], dict[str, str]] | None = None,
) -> int:
    args = parser().parse_args(argv)
    print(DEPRECATION_WARNING, file=sys.stderr)
    try:
        if args.command == "slice":
            result = queue.slice_queue(
                args.target,
                args.export,
                args.state,
                args.branch,
                execute=args.execute,
                identity_reader=identity_reader,
                write_sets=load(args.write_sets, queue.MAX_EXPORT) if args.write_sets else None,
            )
            if result["status"] == "dry-run":
                result = {
                    "status": "dry-run",
                    "binding": result["binding"],
                    "identity_checked": False,
                    "entries": len(result["entries"]),
                    "jobs": len(result["jobs"]),
                    "entry_states": dict(Counter(e["status"] for e in result["entries"])),
                }
        else:
            work = queue.Queue(
                args.state,
                target=args.expect_target,
                branch=args.expect_branch,
                identity_reader=identity_reader,
            )
            if args.command == "next":
                result = {"status": "read-only", "jobs": work.next(args.limit)}
            elif args.command == "monitor":
                result = work.monitor()
            elif args.command == "watch":
                import debt_runner

                for sample in debt_runner.watch(
                    work, interval=args.interval, duration=args.duration
                ):
                    print(
                        json.dumps(sample, sort_keys=True, ensure_ascii=True, allow_nan=False),
                        flush=True,
                    )
                return 0
            elif args.command == "claim":
                result = work.claim(
                    args.job, execute=args.execute, lease_seconds=args.lease_seconds
                )
                if result is None:
                    result = {"status": "no_eligible_job"}
            elif args.command == "complete":
                proposal = load(args.proposal, queue.MAX_RESULT) if args.execute else None
                result = work.complete(proposal, execute=args.execute)
            elif args.command == "defer":
                result = work.defer(args.job, args.reason, execute=args.execute)
            elif args.command == "reconcile":
                receipt = load(args.receipt, queue.MAX_CONTEXT) if args.execute else None
                result = work.reconcile(receipt, effects=args.effects, execute=args.execute)
            elif args.command == "configure":
                import debt_executor

                result = debt_executor.configure(
                    work,
                    load(args.checks, queue.MAX_EXPORT),
                    approved_sha256=args.approve_checks_sha256,
                    execute=args.execute,
                )
            elif args.command == "integrate":
                import debt_executor

                result = debt_executor.integrate(work, args.job, execute=args.execute)
            elif args.command == "run":
                import debt_runner

                result = debt_runner.run(
                    work,
                    provider=args.provider,
                    execute=args.execute,
                    resume=args.resume,
                    integrate=args.integrate,
                    limit=args.limit,
                    max_batches=args.max_batches,
                    wall_seconds=args.wall_seconds,
                    inbox=args.inbox,
                    profile=load(args.profile, queue.MAX_CONTEXT) if args.profile else None,
                )
            else:
                result = work.document(execute=args.execute)
        print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False))
        return (
            2
            if result.get("status") in ("unavailable", "quarantined", "reconciliation_required")
            else 0
        )
    except (queue.Blocked, OSError, ValueError, TypeError, KeyError) as error:
        reason = str(error) if isinstance(error, queue.Blocked) else type(error).__name__
        print(json.dumps({"status": "blocked", "reason": reason}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled", "reason": "operator_cancelled"}))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
