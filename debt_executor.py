"""Serial, explicitly configured local integration. No Sonar or provider calls."""

import ctypes
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from ctypes import wintypes as w
from pathlib import Path
from typing import Any

import debt_queue as q
from debt_transport import run_process

CONTROL_ROOT = Path(__file__).resolve().parent / ".debt-control"


class Contaminated(q.Blocked):
    """The target no longer matches the authorized snapshot."""


def relative_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z0-9_. /-]{1,500}", name)
        or name.startswith("/")
        or any(p in ("", ".", "..") or p.endswith((" ", ".")) for p in name.split("/"))
    ):
        raise q.Blocked("invalid_configured_relative_path")
    return name


def snapshot(root: Path) -> dict[str, str]:
    """Hash the target sources; exclude Git metadata and regenerated outputs."""
    root = q.local_path(root, exists=True)
    files, total, visited = {}, 0, 0
    # Machine-regenerated directories are not bound: build outputs are
    # rewritten by the very checks the harness runs (hashing them would make
    # every configure→build→integrate cycle look contaminated), IDE state is
    # locked while editors run, and none of them are legitimate fix targets.
    # Declared generated files stay governed by allowed_outputs instead.
    volatile = {"bin", "obj", ".vs", ".idea", "TestResults", "node_modules"}
    for base, dirs, names in os.walk(root, followlinks=False):
        if Path(base) == root:
            dirs[:] = [name for name in dirs if name != ".git"]
            names = [name for name in names if name != ".git"]
        dirs[:] = [name for name in dirs if name not in volatile]
        for name in sorted(dirs + names):
            path = q.local_path(Path(base) / name, exists=True)
            visited += 1
            if visited > 20000:
                raise q.Blocked("target_snapshot_entry_budget")
            key = path.relative_to(root).as_posix()
            if path.is_dir():
                files[key] = "directory"
            else:
                try:
                    data = q.read_bytes(path, q.MAX_SOURCE)
                except OSError as error:
                    # Locked/unreadable files (IDE indexes, running outputs)
                    # must not crash the binding: record a deterministic
                    # marker instead. If the file later becomes readable, the
                    # snapshot legitimately differs and re-slice is required.
                    files[key] = f"unreadable:{type(error).__name__}"
                    continue
                total += len(data)
                if total > 256 * 1024 * 1024:
                    raise q.Blocked("target_snapshot_byte_budget")
                files[key] = q.digest(data)
    return files


def validate_config(config: dict[str, Any]) -> Path:
    fields = {
        "version",
        "target",
        "branch",
        "policy",
        "characterization_reason",
        "test_paths",
        "expected_red",
        "checks",
        "allowed_outputs",
    }
    if (
        not isinstance(config, dict)
        or set(config) != fields
        or type(config["version"]) is not int
        or config["version"] != 1
        or config["policy"] not in ("red-first", "characterization")
        or not isinstance(config["characterization_reason"], str)
        or len(config["characterization_reason"]) > 1000
    ):
        raise q.Blocked(
            "invalid_execution_config: keys must be exactly "
            f"{sorted(fields)}; see docs/checks-reference.md"
        )
    root = q.local_path(q.canonical_case(config["target"]), exists=True)
    for key in ("test_paths", "allowed_outputs"):
        values = config[key]
        if (
            not isinstance(values, list)
            or len(values) > 2000
            or not all(isinstance(v, str) for v in values)
            or len({v.casefold() for v in values}) != len(values)
        ):
            raise q.Blocked(
                f"invalid_execution_paths: {key} must be a list of <=2000 unique strings"
            )
        for value in values:
            relative_name(value)
            q.local_path(root / value)
    if set(config["test_paths"]) & set(config["allowed_outputs"]):
        raise q.Blocked("tests_cannot_be_generated_output_exclusions")
    for name in config["allowed_outputs"]:
        if not any(
            p in ("bin", "obj", "artifacts") for p in name.split("/")[:-1]
        ) or not name.endswith((".dll", ".pdb", ".cache", ".deps.json", ".runtimeconfig.json")):
            raise q.Blocked("only_exact_generated_binary_or_runtime_outputs_allowed")
    red = config["expected_red"]
    if (
        not isinstance(red, dict)
        or len(red) > 100
        or any(
            not isinstance(k, str)
            or not 1 <= len(k) <= 500
            or not isinstance(v, str)
            or not 8 <= len(v) <= 500
            or not v.startswith(("Assert.", "AssertionError:", "Expected"))
            for k, v in red.items()
        )
    ):
        raise q.Blocked(
            "invalid_expected_assertions: at most 100 test-name to assertion-marker pairs; "
            "each marker must start with Assert., AssertionError: or Expected"
        )
    if config["policy"] == "red-first" and (not red or not config["test_paths"]):
        raise q.Blocked("red_first_requires_exact_assertions_and_test_paths")
    if config["policy"] == "characterization" and (
        not config["characterization_reason"].strip() or red
    ):
        raise q.Blocked("characterization_requires_explicit_reason_without_red_claim")
    checks = config["checks"]
    if not isinstance(checks, list) or not 2 <= len(checks) <= 8:
        raise q.Blocked(
            f"configured_build_and_trx_checks_required: need 2..8 checks, got {len(checks) if isinstance(checks, list) else type(checks).__name__}"
        )
    seen = set()
    for index, check in enumerate(checks):
        keys = {"name", "kind", "argv", "executable_sha256", "cwd", "timeout_seconds"}
        if isinstance(check, dict) and check.get("kind") == "trx":
            keys.add("report")
        if (
            not isinstance(check, dict)
            or set(check) != keys
            or check["kind"] not in ("build", "trx")
            or not isinstance(check["name"], str)
            or not q.TOKEN.fullmatch(check["name"])
            or check["name"].casefold() in seen
            or type(check["timeout_seconds"]) is not int
            or not 1 <= check["timeout_seconds"] <= 1800
        ):
            raise q.Blocked(
                f"invalid_configured_check: checks[{index}] needs exactly {sorted(keys)}, "
                "kind build|trx, TOKEN name, timeout_seconds int 1..1800"
            )
        seen.add(check["name"].casefold())
        argv = check["argv"]
        if (
            not isinstance(argv, list)
            or not 1 <= len(argv) <= 64
            or any(not isinstance(a, str) or len(a) > 8192 or "\0" in a for a in argv)
            or not Path(argv[0]).is_absolute()
            or Path(argv[0]).suffix.lower() != ".exe"
            or Path(argv[0]).stem.lower() in ("git", "ssh", "sonar-scanner")
        ):
            raise q.Blocked(
                f"invalid_check_argv: checks[{index}].argv must be 1..64 strings, "
                "argv[0] an absolute .exe that is not git/ssh/sonar-scanner"
            )
        if any(
            re.search(r"(?i)(sonar\.(token|login)|--password|--token|authorization:)", a)
            for a in argv
        ):
            raise q.Blocked(
                f"credential_arguments_forbidden: checks[{index}].argv must not carry secrets"
            )
        if q.digest(q.read_bytes(argv[0], q.MAX_SOURCE)) != check["executable_sha256"]:
            raise q.Blocked(
                f"check_executable_hash_mismatch: checks[{index}].argv[0]={argv[0]} "
                "does not match executable_sha256; recompute the sha256 of those exact bytes"
            )
        cwd = root if check["cwd"] == "." else root / relative_name(check["cwd"])
        if not q.local_path(cwd, exists=True).is_dir():
            raise q.Blocked(f"check_cwd_unavailable: checks[{index}].cwd={check['cwd']!r}")
        if check["kind"] == "trx":
            relative_name(check["report"])
            if "/" in check["report"] or not check["report"].endswith(".trx"):
                raise q.Blocked(
                    f"check_report_must_be_direct_trx_file: checks[{index}].report "
                    "must be a bare filename ending in .trx"
                )
    if {c["kind"] for c in checks} != {"build", "trx"} or checks[0]["kind"] != "build":
        raise q.Blocked("configured_build_before_trx_required")
    if config["policy"] == "red-first" and sum(c["kind"] == "trx" for c in checks) != 1:
        raise q.Blocked("red_first_requires_one_exact_trx_check")
    return root


def configure(
    work: q.Queue,
    config: dict[str, Any],
    *,
    approved_sha256: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    root = validate_config(config)
    sha = q.digest(q.encoded(config))
    if not execute:
        return {"status": "dry-run", "checks_sha256": sha, "policy": config["policy"]}
    if approved_sha256 != sha:
        raise q.Blocked(
            "explicit_reviewed_checks_hash_required: recompute with dry-run "
            f"`configure --checks <file>` (got {approved_sha256!r}, need {sha})"
        )
    with work._open(write=True, identity=True) as (connection, binding):
        if str(root) != binding["root"] or config["branch"] != binding["branch"]:
            raise q.Blocked(
                "execution_config_binding_mismatch: "
                f"config target={root} branch={config['branch']!r} vs "
                f"queue root={binding['root']} branch={binding['branch']!r}"
            )
        if connection.execute("SELECT 1 FROM meta WHERE key='executor'").fetchone():
            raise q.Blocked("execution_config_already_bound")
        state = {"config": config, "config_sha256": sha, "snapshot": snapshot(root)}
        payload = q.encoded(state)
        work._reserve(len(payload))
        q.write_immutable(work.state / "execution-config.json", payload)
        state["initial_artifact_sha256"] = q.digest(payload)
        connection.execute("INSERT INTO meta VALUES (?,?)", ("executor", q.encoded(state).decode()))
    return {
        "status": "configured",
        "checks_sha256": sha,
        "snapshot_sha256": q.digest(q.encoded(state["snapshot"])),
    }


class TargetBarrier:
    """Cross-queue Windows mutex plus pack-local durable interruption barrier."""

    def __init__(self, root: str | Path, control_root: str | Path | None = None) -> None:
        control = q.local_path(control_root or CONTROL_ROOT)
        q.state_path(control, root)
        self.folder = control / q.digest(str(root).casefold().encode())
        self.name = "Global\\AgentesDebtTarget-" + self.folder.name
        self.handle = None
        self.active = False

    def __enter__(self) -> "TargetBarrier":
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, restype in (
            ("CreateMutexW", [ctypes.c_void_p, w.BOOL, w.LPCWSTR], w.HANDLE),
            ("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD),
            ("ReleaseMutex", [w.HANDLE], w.BOOL),
            ("CloseHandle", [w.HANDLE], w.BOOL),
        ):
            method = getattr(self.kernel, name)
            method.argtypes, method.restype = args, restype
        self.handle = self.kernel.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise q.Blocked("target_barrier_unavailable")
        status = self.kernel.WaitForSingleObject(self.handle, 0)
        if status != 0:
            if status == 128:
                self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise q.Blocked("target_busy_or_abandoned")
        try:
            q.local_path(self.folder)
            if (self.folder / "active.json").exists() or (self.folder / "quarantine.json").exists():
                raise q.Blocked("target_quarantined_or_interrupted")
            self.folder.mkdir(parents=True, exist_ok=True)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def activate(self, intent: dict[str, Any]) -> None:
        q.write_immutable(self.folder / "active.json", q.encoded(intent))
        self.active = True

    def quarantine(self, reason: str) -> None:
        q.write_immutable(self.folder / "quarantine.json", q.encoded({"reason": reason}))

    def finish(self) -> None:
        if self.active:
            q.local_path(self.folder / "active.json", exists=True).unlink()
            self.active = False

    def __exit__(self, *_: object) -> None:
        if self.handle:
            self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _allowed_changes(config: dict[str, Any]) -> set[str]:
    paths = set(config["allowed_outputs"])
    for name in config["allowed_outputs"]:
        paths.update(p.as_posix() for p in Path(name).parents if str(p) != ".")
    return paths


def _assert_snapshot(
    root: Path,
    expected: dict[str, str],
    config: dict[str, Any],
    *,
    generated: bool = False,
) -> dict[str, str]:
    try:
        actual = snapshot(root)
    except (q.Blocked, OSError) as error:
        raise Contaminated("target_snapshot_unavailable") from error
    changed = {p for p in actual.keys() | expected.keys() if actual.get(p) != expected.get(p)}
    if changed - (_allowed_changes(config) if generated else set()):
        raise Contaminated("unexpected_target_write_or_stale_snapshot")
    return actual


def _checks(
    work: q.Queue,
    binding: dict[str, Any],
    config: dict[str, Any],
    folder: Path,
    phase: str,
    expected: dict[str, str],
    runner: Callable[..., dict[str, Any]],
    deadline: float,
    *,
    red: bool = False,
) -> dict[str, str]:
    def identity() -> None:
        try:
            q.check_identity(binding, work.identity_reader)
        except (q.Blocked, OSError) as error:
            raise Contaminated("target_identity_changed_during_checks") from error

    receipts = []
    for check in config["checks"]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise q.Blocked("execution_wall_clock_limit")
        run = q.local_path(folder / (phase + "-" + check["name"]))
        run.mkdir()
        argv = [
            a.replace("{run}", str(run)).replace("{target}", binding["root"]) for a in check["argv"]
        ]
        cwd = Path(binding["root"]) / check["cwd"]
        identity()
        started = time.time_ns()
        try:
            result = runner(argv, cwd, timeout=min(check["timeout_seconds"], remaining))
        finally:
            expected = _assert_snapshot(Path(binding["root"]), expected, config, generated=True)
            identity()
        if result.get("reason") or result.get("status") != "exited":
            raise q.Blocked("configured_check_process_failed")
        receipt = {"name": check["name"], "kind": check["kind"], "exit_code": result["exit_code"]}
        if check["kind"] == "trx":
            report = run / check["report"]
            receipt["tests"] = parse_trx(
                report, started, result["exit_code"], config["expected_red"] if red else None
            )
            receipt["report"] = report.relative_to(work.state).as_posix()
            receipt["report_sha256"] = q.digest(q.read_bytes(report, q.MAX_EXPORT))
        elif result["exit_code"] != 0:
            raise q.Blocked("configured_build_failed")
        receipts.append(receipt)
        work._reserve(0)
    q.write_immutable(folder / (phase + ".json"), q.encoded(receipts))
    return expected


def _replacement_bytes(original: bytes, edit: dict[str, Any]) -> bytes:
    text = original.decode("utf-8")
    replacements = [(text.index(r["old"]), r["old"], r["new"]) for r in edit["replacements"]]
    for start, old, new in sorted(replacements, reverse=True):
        text = text[:start] + new + text[start + len(old) :]
    result = text.encode("utf-8")
    if original.startswith(b"\xef\xbb\xbf") != result.startswith(b"\xef\xbb\xbf"):
        raise q.Blocked("bom_change_forbidden")
    if b"\r\n" in original and re.search(b"(?<!\r)\n", result):
        raise q.Blocked("newline_style_change_forbidden")
    if len(result) > q.MAX_SOURCE:
        raise q.Blocked("edited_file_budget_exceeded")
    return result


def _apply(
    root: Path,
    edits: list[dict[str, Any]],
    phase: str,
    originals: dict[str, bytes],
    expected: dict[str, str],
) -> dict[str, str]:
    expected = dict(expected)
    for edit in edits:
        if edit.get("phase", "implementation") != phase:
            continue
        name = edit["path"]
        path = q.source_path(root, name)
        if q.read_bytes(path, q.MAX_SOURCE) != originals[name]:
            raise Contaminated("source_changed_before_apply")
        data = _replacement_bytes(originals[name], edit)
        # In-place writes retain existing filesystem metadata. The write-ahead
        # preimage and durable target barrier handle interrupted/partial writes.
        with path.open("r+b") as stream:
            stream.write(data)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        expected[name] = q.digest(data)
    return expected


def integrate(
    work: q.Queue,
    job_id: str,
    *,
    execute: bool = False,
    control_root: str | Path | None = None,
    process_runner: Callable[..., dict[str, Any]] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    if not execute:
        return {"status": "dry-run", "action": "integrate", "job_id": job_id}
    runner = process_runner or run_process
    deadline = time.monotonic() + 3600 if deadline is None else deadline
    with work._open() as (connection, binding):
        row = connection.execute("SELECT value FROM meta WHERE key='executor'").fetchone()
        if not row:
            raise q.Blocked("reviewed_execution_config_required")
        configured = q.parse_json(row[0])
        config = configured["config"]
        root = validate_config(config)
        if (
            q.digest(q.read_bytes(work.state / "execution-config.json", 8 * q.MAX_EXPORT))
            != configured["initial_artifact_sha256"]
        ):
            raise q.Blocked("execution_config_artifact_mismatch")
    with TargetBarrier(root, control_root) as barrier:
        with work._open(write=True, identity=True) as (connection, binding):
            configured = q.parse_json(
                connection.execute("SELECT value FROM meta WHERE key='executor'").fetchone()[0]
            )
            expected = _assert_snapshot(root, configured["snapshot"], config)
            job = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (work._id(job_id),)
            ).fetchone()
            if not job:
                raise q.Blocked("unknown_job")
            if job["status"] == "locally_verified":
                return {"status": "already_locally_verified", "job_id": job_id}
            if job["status"] != "proposed":
                raise q.Blocked("only_proposed_jobs_can_integrate")
            attempt = connection.execute(
                "SELECT * FROM attempts WHERE job_id=? ORDER BY number DESC", (job_id,)
            ).fetchone()
            folder = work._attempt_folder(attempt)
            result = q.parse_json(q.read_bytes(folder / "result.json", 2 * q.MAX_RESULT))
            proposal = result["proposal"]
            context = q.parse_json(q.read_bytes(folder / "job.json", q.MAX_CONTEXT))
            work._bound_attempt(connection, proposal)
            work._validate_proposal(proposal, context, binding)
            test_edits = [edit for edit in proposal["edits"] if edit.get("phase") == "test"]
            implementation = [
                edit
                for edit in proposal["edits"]
                if edit.get("phase", "implementation") == "implementation"
            ]
            if (
                not implementation
                or (config["policy"] == "red-first" and not test_edits)
                or (config["policy"] == "characterization" and test_edits)
                or any(edit["path"] not in config["test_paths"] for edit in test_edits)
                or any(edit["path"] in config["test_paths"] for edit in implementation)
            ):
                work._set_status(
                    connection, job_id, "deferred", "proposal_has_no_approved_behavioral_proof_plan"
                )
                return {
                    "status": "deferred",
                    "job_id": job_id,
                    "reason": "proposal_has_no_approved_behavioral_proof_plan",
                }
            if set(context["write_paths"]) & set(config["allowed_outputs"]):
                raise q.Blocked("write_set_cannot_be_snapshot_exclusion")
            originals = {
                edit["path"]: q.read_bytes(q.source_path(root, edit["path"]), q.MAX_SOURCE)
                for edit in proposal["edits"]
            }
            for edit in proposal["edits"]:
                _replacement_bytes(originals[edit["path"]], edit)
            integration = q.local_path(folder / "integration")
            if integration.exists():
                raise q.Blocked("integration_journal_exists_manual_review_required")
            work._reserve(sum(map(len, originals.values())) + len(q.encoded(expected)))
            integration.mkdir()
            for name, data in originals.items():
                q.write_immutable(integration / (q.digest(name.encode()) + ".preimage"), data)
            intent = {
                "job_id": job_id,
                "attempt_id": attempt["id"],
                "queue": str(work.state),
                "config_sha256": configured["config_sha256"],
                "before": expected,
                "result_sha256": attempt["result_sha"],
                "write_paths": sorted(originals),
            }
            q.write_immutable(integration / "intent.json", q.encoded(intent))
            barrier.activate(
                {
                    "job_id": job_id,
                    "queue": str(work.state),
                    "intent": str(integration / "intent.json"),
                }
            )
            phase = "baseline"
            try:
                expected = _checks(
                    work, binding, config, integration, phase, expected, runner, deadline
                )
                if config["policy"] == "red-first":
                    phase = "red"
                    expected = _apply(root, proposal["edits"], "test", originals, expected)
                    _assert_snapshot(root, expected, config)
                    expected = _checks(
                        work,
                        binding,
                        config,
                        integration,
                        phase,
                        expected,
                        runner,
                        deadline,
                        red=True,
                    )
                phase = "green"
                expected = _apply(root, proposal["edits"], "implementation", originals, expected)
                _assert_snapshot(root, expected, config)
                work._set_status(connection, job_id, "applied")
                expected = _checks(
                    work, binding, config, integration, phase, expected, runner, deadline
                )
                phase = "post-batch"
                expected = _checks(
                    work, binding, config, integration, phase, expected, runner, deadline
                )
                proof = {
                    "status": "locally_verified",
                    "job_id": job_id,
                    "policy": config["policy"],
                    "config_sha256": configured["config_sha256"],
                    "after": expected,
                    "sonar_confirmed": False,
                    "notice": "Local configured checks only; not authenticated or global quality proof.",
                }
                q.write_immutable(integration / "verification.json", q.encoded(proof))
                artifacts = {
                    path.relative_to(work.state).as_posix(): q.digest(
                        q.read_bytes(path, q.MAX_SOURCE)
                    )
                    for path in integration.rglob("*")
                    if path.is_file()
                }
                connection.execute(
                    "INSERT INTO meta VALUES (?,?)",
                    ("verification:" + attempt["id"], q.encoded(artifacts).decode()),
                )
                configured["snapshot"] = expected
                connection.execute(
                    "UPDATE meta SET value=? WHERE key='executor'",
                    (q.encoded(configured).decode(),),
                )
                work._set_status(connection, job_id, "locally_verified")
                connection.execute(
                    "UPDATE attempts SET status='locally_verified' WHERE id=?", (attempt["id"],)
                )
                outcome = {
                    "status": "locally_verified",
                    "job_id": job_id,
                    "evidence": str(integration / "verification.json"),
                }
            except Exception as error:
                reason = (
                    str(error) if isinstance(error, q.Blocked) else "integration_operation_failed"
                )
                q.write_immutable(
                    integration / "failure.json", q.encoded({"phase": phase, "reason": reason})
                )
                if (
                    phase == "baseline"
                    and isinstance(error, q.Blocked)
                    and not isinstance(error, Contaminated)
                ):
                    work._set_status(connection, job_id, "deferred", reason)
                    outcome = {"status": "deferred", "job_id": job_id, "reason": reason}
                else:
                    work._set_status(connection, job_id, "failed", reason)
                    connection.execute("UPDATE meta SET value='true' WHERE key='quarantined'")
                    barrier.quarantine(reason)
                    outcome = {"status": "quarantined", "job_id": job_id, "reason": reason}
        if outcome["status"] != "quarantined":
            barrier.finish()  # Clear only after the SQLite transaction committed.
        return outcome


def parse_trx(
    path: str | Path,
    started_ns: int,
    exit_code: int,
    expected_red: dict[str, str] | None = None,
) -> dict[str, Any]:
    path = q.local_path(path, exists=True)
    raw = q.read_bytes(path, q.MAX_EXPORT)
    if (
        not started_ns <= path.stat().st_mtime_ns <= time.time_ns()
        or type(exit_code) is not int
        or b"<!DOCTYPE" in raw.upper()
        or b"<!ENTITY" in raw.upper()
    ):
        raise q.Blocked("stale_or_unsafe_trx")
    try:
        root = ET.fromstring(raw)

        def nodes(tag: str) -> list[ET.Element]:
            return [n for n in root.iter() if n.tag.rsplit("}", 1)[-1] == tag]

        counters, summaries, results = (
            nodes("Counters"),
            nodes("ResultSummary"),
            nodes("UnitTestResult"),
        )
        if len(counters) != 1 or len(summaries) != 1:
            raise ValueError()
        values = {
            name: int(counters[0].attrib[name])
            for name in ("total", "executed", "passed", "failed", "notExecuted")
        }
        if (
            any(v < 0 for v in values.values())
            or values["total"] <= 0
            or values["notExecuted"] != 0
            or values["executed"] != values["total"]
            or values["passed"] + values["failed"] != values["total"]
            or len(results) != values["total"]
        ):
            raise ValueError()
        failures, seen = {}, set()
        for result in results:
            name, outcome = result.attrib["testName"], result.attrib["outcome"]
            if not name or len(name) > 500 or name in seen or outcome not in ("Passed", "Failed"):
                raise ValueError()
            seen.add(name)
            if outcome == "Failed":
                messages = [
                    n.text or "" for n in result.iter() if n.tag.rsplit("}", 1)[-1] == "Message"
                ]
                if len(messages) != 1:
                    raise ValueError()
                failures[name] = messages[0]
        if len(failures) != values["failed"]:
            raise ValueError()
        if expected_red is not None:
            if (
                exit_code != 1
                or not failures
                or set(failures) != set(expected_red)
                or summaries[0].get("outcome") != "Failed"
                or any(
                    not failures[name].startswith(marker) for name, marker in expected_red.items()
                )
            ):
                raise ValueError()
        elif (
            exit_code != 0 or failures or summaries[0].get("outcome") not in ("Completed", "Passed")
        ):
            raise ValueError()
        return dict(values, expected_red=expected_red is not None, failed_tests=sorted(failures))
    except (ET.ParseError, KeyError, ValueError, TypeError) as error:
        raise q.Blocked("trx_counters_outcomes_or_assertion_provenance_invalid") from error
