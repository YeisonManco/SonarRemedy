"""Bounded manual or explicitly selected tool-free native proposal batches."""

import math
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import debt_executor as executor
import debt_queue as q
from debt_transport import capability, dispatch, preflight


def active_attempts(work: q.Queue) -> list[dict[str, Any]]:
    with work._open() as (connection, _):
        rows = connection.execute("""SELECT a.*, j.kind, j.status AS job_status FROM attempts a
            JOIN jobs j ON j.id=a.job_id WHERE j.status IN ('leased','proposed')
            AND a.status IN ('leased','proposed')""").fetchall()
        rows = sorted(rows, key=lambda row: (q.KINDS.index(row["kind"]), row["job_id"]))
        return [
            {
                "status": row["job_status"],
                "kind": row["kind"],
                "job_id": row["job_id"],
                "attempt_id": row["id"],
                "lease": row["lease"],
                "context_fingerprint": row["fingerprint"],
                "expires_at": row["expires"],
                "context_path": str(work._attempt_folder(row) / "job.json"),
            }
            for row in rows
        ]


def _invalid_result(receipt: dict[str, Any]) -> dict[str, Any]:
    return dict(
        version=1,
        **{k: receipt[k] for k in ("job_id", "attempt_id", "lease", "context_fingerprint")},
        status="failed",
        edits=[],
        reason="invalid_manual_proposal",
        risks=[],
        follow_up=[],
        test_plan="No behavioral proof; the script rejected the supplied proposal.",
    )


def _record_native_failure(work: q.Queue, receipt: dict[str, Any], reason: str) -> dict[str, Any]:
    failure = _invalid_result(receipt)
    failure["reason"] = (
        reason
        if isinstance(reason, str) and q.TOKEN.fullmatch(reason)
        else "native_proposal_failed"
    )
    failure["test_plan"] = (
        "No behavioral proof; the native proposal process failed or was rejected."
    )
    return work.complete(failure, execute=True)


def _bind_profile(work: q.Queue, profile: dict[str, Any]) -> None:
    data = q.encoded(profile)
    sha = q.digest(data)
    with work._open(write=True, identity=True) as (connection, _):
        existing = connection.execute(
            "SELECT value FROM meta WHERE key='native_profile'"
        ).fetchone()
        if existing:
            if (
                existing[0] != sha
                or q.read_bytes(work.state / "native-profile.json", q.MAX_CONTEXT) != data
            ):
                raise q.Blocked("native_profile_changed_choose_new_run")
        else:
            work._reserve(len(data))
            q.write_immutable(work.state / "native-profile.json", data)
            connection.execute("INSERT INTO meta VALUES (?,?)", ("native_profile", sha))


def _native_batch(
    work: q.Queue,
    receipts: list[dict[str, Any]],
    profile: dict[str, Any],
    acceptance: dict[str, Any],
    native_runner: Callable[..., dict[str, Any]],
    control_root: str | Path | None,
    deadline: float,
) -> dict[str, dict[str, Any]]:
    """Workers touch only their attempt artifacts; all finish before DB completion/apply."""
    with work._open() as (_, binding):
        root = Path(binding["root"])
    results, cancel = {}, threading.Event()
    with executor.TargetBarrier(root, control_root) as barrier:
        before = executor.snapshot(root)
        q.check_identity(binding, work.identity_reader)
        try:
            with ThreadPoolExecutor(max_workers=len(receipts)) as pool:
                futures = {
                    pool.submit(
                        dispatch,
                        profile,
                        receipt,
                        acceptance=acceptance,
                        process_runner=native_runner,
                        deadline=deadline,
                        cancel_event=cancel,
                    ): receipt
                    for receipt in receipts
                }
                try:
                    for future in as_completed(futures):
                        receipt = futures[future]
                        try:
                            results[receipt["attempt_id"]] = future.result()
                        except Exception:
                            results[receipt["attempt_id"]] = {
                                "status": "failed",
                                "reason": "native_dispatch_failed",
                            }
                except BaseException:
                    cancel.set()
                    raise
        finally:
            try:
                after = executor.snapshot(root)
                q.check_identity(binding, work.identity_reader)
                if after != before:
                    raise q.Blocked("proposal_worker_changed_target")
            except (q.Blocked, OSError):
                barrier.quarantine("proposal_worker_target_contamination")
                with work._open(write=True) as (connection, _):
                    connection.execute("UPDATE meta SET value='true' WHERE key='quarantined'")
                raise q.Blocked("proposal_worker_target_contamination") from None
    return results


def _run_native(
    work: q.Queue,
    profile: dict[str, Any],
    *,
    resume: bool,
    integrate: bool,
    limit: int,
    max_batches: int,
    deadline: float,
    native_runner: Callable[..., dict[str, Any]],
    process_runner: Callable[..., dict[str, Any]],
    control_root: str | Path | None,
) -> dict[str, Any]:
    existing = active_attempts(work)
    if existing and not resume:
        raise q.Blocked("resume_required_for_existing_attempts")
    if not existing and not work.next(limit):
        return {"status": "quiescent", "provider": "claude", "batches": 0, "processed": 0}
    with work._open() as (connection, _):
        row = connection.execute("SELECT value FROM meta WHERE key='executor'").fetchone()
        if integrate and not row:
            raise q.Blocked("reviewed_execution_config_required")
        config = q.parse_json(row[0])["config"] if row else {}
        acceptance = {k: config[k] for k in ("policy", "test_paths", "expected_red") if k in config}
    checked = preflight(profile, work.state, process_runner=native_runner, deadline=deadline)
    if checked["status"] != "available":
        return dict(checked, provider="claude")
    _bind_profile(work, profile)
    batches, processed = 0, 0
    ready = {a["job_id"] for a in existing if a["status"] == "proposed"} if not integrate else set()

    def outcome(status: str, **fields: Any) -> dict[str, Any]:
        return dict(
            status=status, provider="claude", batches=batches, processed=processed, **fields
        )

    while True:
        if time.monotonic() >= deadline:
            return outcome("wall_clock_limit")
        active = [a for a in active_attempts(work) if a["job_id"] not in ready]
        pending = work.next(limit) if not active else []
        if not active and not pending:
            return outcome(
                "proposals_ready" if ready else "quiescent",
                proposals_ready=len(ready),
                notice="No eligible work; local results do not confirm Sonar improvement.",
            )
        if batches >= max_batches:
            return outcome("batch_limit")
        batches += 1
        if not active:
            for job in pending:
                if time.monotonic() >= deadline:
                    return outcome("wall_clock_limit")
                work.claim(job["job_id"], execute=True)
            active = [a for a in active_attempts(work) if a["job_id"] not in ready]
        if not active:
            continue
        active = [a for a in active if a["kind"] == active[0]["kind"]][:limit]
        leased = [a for a in active if a["status"] == "leased"]
        if any(time.time() >= a["expires_at"] for a in leased):
            return outcome("reconciliation_required")
        try:
            results = (
                _native_batch(
                    work, leased, profile, acceptance, native_runner, control_root, deadline
                )
                if leased
                else {}
            )
        except q.Blocked as error:
            if str(error) == "proposal_worker_target_contamination":
                return outcome("quarantined", reason=str(error))
            raise
        # Deterministic controlled order, never completion order from the futures.
        for receipt in active:
            if receipt["status"] == "leased":
                result = results[receipt["attempt_id"]]
                if result["status"] == "interrupted" or time.time() >= receipt["expires_at"]:
                    return outcome("reconciliation_required", job_id=receipt["job_id"])
                try:
                    recorded = (
                        work.complete(result["proposal"], execute=True)
                        if result["status"] == "completed"
                        else _record_native_failure(work, receipt, result.get("reason"))
                    )
                except (q.Blocked, ValueError, TypeError, KeyError):
                    recorded = _record_native_failure(work, receipt, "invalid_native_proposal")
                processed += 1
                if recorded["status"] != "proposed":
                    continue
            if integrate:
                if time.monotonic() >= deadline:
                    return outcome("wall_clock_limit")
                result = executor.integrate(
                    work,
                    receipt["job_id"],
                    execute=True,
                    control_root=control_root,
                    process_runner=process_runner,
                    deadline=deadline,
                )
                if result["status"] == "quarantined":
                    return outcome("quarantined", job_id=receipt["job_id"], reason=result["reason"])
            else:
                ready.add(receipt["job_id"])


def run(
    work: q.Queue,
    *,
    provider: str = "manual",
    execute: bool = False,
    resume: bool = False,
    integrate: bool = False,
    limit: int = 4,
    max_batches: int = 10,
    wall_seconds: float = 3600,
    inbox: str | Path | None = None,
    proposal_factory: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    process_runner: Callable[..., dict[str, Any]] | None = None,
    control_root: str | Path | None = None,
    profile: dict[str, Any] | None = None,
    native_process_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The optional factory/check callbacks are local test seams, not CLI providers."""
    start = time.monotonic()
    if (
        type(limit) is not int
        or not 1 <= limit <= 8
        or type(max_batches) is not int
        or not 1 <= max_batches <= 1000
        or type(wall_seconds) not in (int, float)
        or not math.isfinite(wall_seconds)
        or not 0 < wall_seconds <= 86400
    ):
        raise q.Blocked("invalid_runner_bounds")
    if not execute:
        return {
            "status": "dry-run",
            "provider": provider,
            "limit": limit,
            "max_batches": max_batches,
            "wall_seconds": wall_seconds,
            "integrate": integrate,
            "notice": "No files or processes; queue resume only.",
        }
    available = capability(provider, profile)
    if available["status"] != "available":
        return available
    if provider != "manual":
        return _run_native(
            work,
            profile,
            resume=resume,
            integrate=integrate,
            limit=limit,
            max_batches=max_batches,
            deadline=start + wall_seconds,
            native_runner=native_process_runner,
            process_runner=process_runner,
            control_root=control_root,
        )
    existing = active_attempts(work)
    if existing and not resume:
        raise q.Blocked("resume_required_for_existing_attempts")
    with work._open() as (connection, binding):
        if (
            integrate
            and not connection.execute("SELECT 1 FROM meta WHERE key='executor'").fetchone()
        ):
            raise q.Blocked("reviewed_execution_config_required")
        folder = q.state_path(inbox or work.state / "inbox", Path(binding["root"]))
    batches, completed = 0, 0
    deadline = start + wall_seconds

    def outcome(status: str, **fields: Any) -> dict[str, Any]:
        return dict(
            status=status, provider="manual", batches=batches, processed=completed, **fields
        )

    while True:
        if time.monotonic() >= deadline:
            return outcome("wall_clock_limit")
        active = active_attempts(work)
        pending = work.next(limit) if not active else []
        if not active and not pending:
            return outcome("quiescent", notice="No eligible jobs; not a Sonar or quality verdict.")
        if batches >= max_batches:
            return outcome("batch_limit")
        batches += 1
        if not active:
            for job in pending:
                if time.monotonic() >= deadline:
                    return outcome("wall_clock_limit")
                work.claim(job["job_id"], execute=True)
            active = active_attempts(work)
        if not active:
            continue  # For example, an oversized group was deferred intact during claim.
        # Only one kind has active work in this batch. No later stage is guessed
        # independent while a manual response for this stage is outstanding.
        active = [a for a in active if a["kind"] == active[0]["kind"]][:limit]
        waiting, ready = [], []
        for receipt in active:
            if time.monotonic() >= deadline:
                return outcome("wall_clock_limit")
            if receipt["status"] == "leased":
                if time.time() >= receipt["expires_at"]:
                    return outcome("reconciliation_required", job_id=receipt["job_id"])
                path = q.local_path(folder / (receipt["attempt_id"] + ".json"))
                proposal = None
                try:
                    if path.exists():
                        proposal = q.parse_json(q.read_bytes(path, q.MAX_RESULT))
                    elif proposal_factory is not None:
                        # Tests only: fresh bounded input, not a host or general-agent fallback.
                        context = q.parse_json(q.read_bytes(receipt["context_path"], q.MAX_CONTEXT))
                        proposal = proposal_factory(context)
                    else:
                        q.local_path(folder)
                        folder.mkdir(parents=True, exist_ok=True)
                        waiting.append(dict(receipt, proposal_path=str(path)))
                        continue
                    result = work.complete(proposal, execute=True)
                except (q.Blocked, OSError, ValueError, TypeError, KeyError):
                    result = work.complete(_invalid_result(receipt), execute=True)
                completed += 1
                if result["status"] != "proposed":
                    continue
            if integrate:
                result = executor.integrate(
                    work,
                    receipt["job_id"],
                    execute=True,
                    control_root=control_root,
                    process_runner=process_runner,
                    deadline=deadline,
                )
                if result["status"] == "quarantined":
                    return outcome("quarantined", job_id=receipt["job_id"], reason=result["reason"])
            else:
                ready.append(receipt["job_id"])
        if waiting:
            return outcome("awaiting_proposals", waiting=waiting)
        if ready:
            return outcome(
                "proposals_ready",
                jobs=ready,
                notice="Integrate only after explicit check configuration/review.",
            )


def watch(work: q.Queue, *, interval: float = 2, duration: float = 60) -> Iterator[dict[str, Any]]:
    if (
        type(interval) not in (int, float)
        or not math.isfinite(interval)
        or not 0.01 <= interval <= 60
        or type(duration) not in (int, float)
        or not math.isfinite(duration)
        or not 0 < duration <= 3600
    ):
        raise q.Blocked("invalid_watch_bounds")
    deadline = time.monotonic() + duration
    sample = 0
    while time.monotonic() < deadline:
        sample += 1
        yield {
            "status": "watch",
            "sample": sample,
            "progress": work.monitor(timeout=max(0.001, min(0.5, deadline - time.monotonic()))),
        }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
