"""Config-backed .NET local scan. Dry-run by default; no provider dispatch."""

import argparse
import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from debtpack import PACK, linked, read_json, safe_path
from sonar_client import Blocked, Client, collect, endpoint
from sonar_gateway import Gateway


class FailedTests(Blocked):
    """A report proves test failure even when the runner returned zero."""


class Config:
    def __init__(self, raw: dict[str, Any]) -> None:
        allowed = {
            "adapter",
            "repo",
            "solution",
            "url",
            "trusted_url",
            "project",
            "branch",
            "timeout",
            "poll_attempts",
        }
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise Blocked(
                "unsupported configuration field; credentials and skip switches forbidden"
            )
        if raw.get("adapter") != "dotnet":
            raise Blocked("only dotnet adapter implemented; other adapters explicitly unsupported")
        self.url = endpoint(raw["url"])
        if endpoint(raw["trusted_url"]) != self.url:
            raise Blocked("trusted_url must explicitly match the final HTTPS server URL")
        self.repo = Path(raw["repo"]).resolve(strict=True)
        self.solution = safe_path(self.repo, raw["solution"])
        if not self.solution.is_file() or self.solution.suffix not in (".sln", ".slnx", ".csproj"):
            raise Blocked("solution must be an existing .NET solution or project within repo")
        self.project, self.branch = raw["project"], raw["branch"]
        for name in ("project", "branch"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", value):
                raise Blocked("invalid project or branch")
        self.timeout = raw.get("timeout", 600)
        self.poll_attempts = raw.get("poll_attempts", 60)
        if type(self.timeout) is not int or not 1 <= self.timeout <= 3600:
            raise Blocked("timeout must be 1..3600 seconds")
        if type(self.poll_attempts) is not int or not 1 <= self.poll_attempts <= 120:
            raise Blocked("poll_attempts must be 1..120")


def redactor(secret: str) -> Callable[[str], str]:
    variants = {secret, quote(secret, safe=""), base64.b64encode((secret + ":").encode()).decode()}

    def redact(text: str) -> str:
        for value in sorted(variants, key=len, reverse=True):
            if value:
                text = text.replace(value, "[REDACTED]")
        return re.sub(
            r'(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+|sonar\.(?:token|login)\s*=)[^\s"<>]+',
            r"\1[REDACTED]",
            text,
        )

    return redact


def child_environment() -> dict[str, str]:
    # No ambient scanner switches, git repository overrides, or token in children.
    forbidden = ("SONAR", "GIT_", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS")
    return {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(forbidden)
        and k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    }


def snapshot(repo: Path) -> dict[str, Any]:
    def git(*args: str) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=30,
            env=child_environment(),
            check=False,
        )
        if result.returncode:
            raise Blocked("Git snapshot unavailable; repository needs a valid HEAD")
        if len(result.stdout) > 32 * 1024 * 1024:
            raise Blocked("Git evidence budget exceeded")
        return result.stdout

    root = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != repo.resolve():
        raise Blocked("repo must be the Git root")
    revision = git("rev-parse", "HEAD").decode().strip()
    diff = git("diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD", "--")
    names = sorted(
        set(
            git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
        )
        - {""}
    )
    if len(names) > 20000:
        raise Blocked("snapshot file budget exceeded")
    digest = hashlib.sha256(revision.encode())
    size = 0
    files = {}
    for name in names:
        # Build products are deliberately outside the source identity. Ignored inputs
        # are not hermetically captured; document this boundary instead of claiming it.
        if any(
            p in (".sonarqube", ".debt-runs", ".debt-scan.lock", "bin", "obj")
            for p in Path(name).parts
        ):
            continue
        path = repo / name
        if not path.resolve().is_relative_to(repo.resolve()) or (path.exists() and linked(path)):
            raise Blocked("snapshot contains an escaping or linked file")
        digest.update(name.encode() + b"\0")
        if not path.exists():
            digest.update(b"deleted\0")
            files[name] = None
            continue
        if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
            raise Blocked("snapshot file unsupported or too large")
        data = path.read_bytes()
        size += len(data)
        if size > 256 * 1024 * 1024:
            raise Blocked("snapshot byte budget exceeded")
        digest.update(hashlib.sha256(data).digest())
        files[name] = hashlib.sha256(data).hexdigest()
    return {
        "revision": revision,
        "digest": digest.hexdigest(),
        "diff_digest": hashlib.sha256(diff).hexdigest(),
        "files": files,
    }


def run_process(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    log: Path,
    redact: Callable[[str], str],
) -> int:
    """Bound memory/log output and kill the entire process tree on timeout/overflow."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
    )
    output = bytearray()
    overflow = threading.Event()

    def drain() -> None:
        while True:
            chunk = proc.stdout.read(8192)
            if not chunk:
                break
            if len(output) + len(chunk) > 8 * 1024 * 1024:
                overflow.set()
                break
            output.extend(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None or reader.is_alive():
            if overflow.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("process time/output budget exceeded")
            time.sleep(0.02)
        return proc.returncode
    finally:
        if proc.poll() is None or reader.is_alive():
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        reader.join(timeout=5)
        proc.stdout.close()
        log.write_text(
            redact(output.decode("utf-8", errors="replace")) or "(no process output)\n",
            encoding="utf-8",
        )


def fresh(path: Path, started: float) -> bytes:
    if (
        not path.is_file()
        or linked(path)
        or not 0 < path.stat().st_size <= 64 * 1024 * 1024
        or path.stat().st_mtime < started - 2
    ):
        raise Blocked("report missing, stale, linked, empty or exceeds budget")
    return path.read_bytes()


def test_reports(folder: Path, started: float) -> dict[str, int]:
    reports = sorted(folder.rglob("*.trx"))
    if not reports or len(reports) > 100:
        raise Blocked("test reports unavailable or exceed budget")
    counts = {"total": 0, "executed": 0, "passed": 0, "failed": 0, "notExecuted": 0}
    for report in reports:
        root = ET.fromstring(fresh(report, started))
        counters = [e for e in root.iter() if e.tag.split("}")[-1] == "Counters"]
        if len(counters) != 1:
            raise Blocked("test counters missing or ambiguous")
        try:
            values = {key: int(counters[0].attrib[key]) for key in counts}
        except (KeyError, ValueError):
            raise Blocked("test counters invalid") from None
        if any(n < 0 for n in values.values()):
            raise Blocked("negative test counters")
        for key, value in values.items():
            counts[key] += value
        if values["failed"]:
            raise FailedTests("test report proves failing tests")
        if (
            values["total"] != values["executed"]
            or values["passed"] != values["total"]
            or values["notExecuted"]
        ):
            raise Blocked("failed, skipped or incomplete test results; no approval")
    if counts["total"] == 0:
        raise Blocked("zero discovered tests is not a pass")
    coverage = sorted(folder.rglob("coverage.opencover.xml"))
    if not coverage or len(coverage) > 100:
        raise Blocked("fresh OpenCover unavailable or exceeds budget")
    for report in coverage:
        root = ET.fromstring(fresh(report, started))
        summary = root.find("Summary")
        if (
            root.tag != "CoverageSession"
            or summary is None
            or int(summary.get("numSequencePoints", "0")) <= 0
        ):
            raise Blocked("OpenCover has no instrumented sequence points")
    return counts


def task_report(
    repo: Path, started: float, allowed_url: str, project: str, previous: str | None
) -> str:
    path = repo / ".sonarqube/out/.sonar/report-task.txt"
    content = fresh(path, started)
    fields = dict(line.split("=", 1) for line in content.decode().splitlines() if "=" in line)
    task = fields.get("ceTaskId")
    parsed = urlsplit(fields.get("ceTaskUrl", ""))
    expected = urlsplit(allowed_url + "/api/ce/task")
    if (
        fields.get("projectKey") != project
        or fields.get("serverUrl", "").rstrip("/") != allowed_url
        or (parsed.scheme, parsed.netloc, parsed.path)
        != (expected.scheme, expected.netloc, expected.path)
        or parsed.fragment
        or parse_qs(parsed.query) != {"id": [task]}
        or task == previous
    ):
        raise Blocked("scanner report identity, endpoint or freshness mismatch")
    return task


def scan(
    config: Config,
    output: str | Path,
    execute: bool = False,
    runner: Callable[..., int] = run_process,
) -> dict[str, Any]:
    secret = os.environ.get("SONAR_TOKEN", "")
    if not secret or secret.strip() != secret or any(ord(x) < 33 for x in secret):
        raise Blocked("SONAR_TOKEN must be present in the local process environment")
    if not execute:
        return {
            "status": "dry-run",
            "adapter": "dotnet",
            "repo": str(config.repo),
            "solution": str(config.solution),
            "project": config.project,
            "branch": config.branch,
            "url": config.url,
            "stages": [
                "scanner-begin",
                "build",
                "tests/OpenCover",
                "scanner-end/upload",
                "CE/collection",
            ],
            "notice": "No process, network or output writes. --execute authorizes build and remote upload.",
        }
    output = Path(output).resolve()
    if output.is_relative_to(config.repo):
        raise Blocked("run output must be outside target repo to keep snapshot stable")
    output.mkdir(parents=True, exist_ok=True)
    run = output / (time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12])
    run.mkdir()
    started = time.time()
    result = {
        "status": "blocked",
        "stage": "snapshot",
        "run": str(run),
        "started": started,
        "tests": None,
        "reports": {},
    }
    redact = redactor(secret)
    # O_EXCL gives one scanner writer per repository; inspect a stale lock manually.
    lock = config.repo / ".debt-scan.lock"
    owned = False
    try:
        with lock.open("x", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        owned = True
        before = snapshot(config.repo)
        result["snapshot"] = before
        results = run / "results"
        results.mkdir()
        old_report = config.repo / ".sonarqube/out/.sonar/report-task.txt"
        previous = None
        if old_report.is_file():
            previous = dict(
                line.split("=", 1) for line in old_report.read_text().splitlines() if "=" in line
            ).get("ceTaskId")
        client = Client(config.url, secret, min(config.timeout, 60))
        with Gateway(client) as gateway:
            auth = "/d:sonar.token=" + gateway.credential
            commands = [
                (
                    "scanner-begin",
                    [
                        "dotnet",
                        "sonarscanner",
                        "begin",
                        "/k:" + config.project,
                        auth,
                        "/d:sonar.host.url=" + gateway.url,
                        "/d:sonar.branch.name=" + config.branch,
                        "/d:sonar.scm.revision=" + before["digest"],
                        "/d:sonar.scanner.skipJreProvisioning=true",
                        "/d:sonar.cs.opencover.reportsPaths="
                        + str(results / "**/coverage.opencover.xml"),
                    ],
                ),
                (
                    "build",
                    [
                        "dotnet",
                        "build",
                        str(config.solution),
                        "--no-incremental",
                        "--disable-build-servers",
                    ],
                ),
                (
                    "tests",
                    [
                        "dotnet",
                        "test",
                        str(config.solution),
                        "--no-build",
                        "--no-restore",
                        "--logger",
                        "trx",
                        "--results-directory",
                        str(results),
                        "--collect:XPlat Code Coverage",
                        "--",
                        "DataCollectionRunSettings.DataCollectors.DataCollector.Configuration.Format=opencover",
                    ],
                ),
                ("scanner-end", ["dotnet", "sonarscanner", "end", auth]),
            ]
            for stage, argv in commands:
                result["stage"] = stage
                code = runner(
                    argv,
                    config.repo,
                    child_environment(),
                    config.timeout,
                    run / (stage + ".log"),
                    redact,
                )
                result.setdefault("return_codes", {})[stage] = code
                if code != 0:
                    result["status"] = "failed"
                    return result
                if stage == "tests":
                    result["tests"] = test_reports(results, started)
                    if snapshot(config.repo) != before:
                        raise Blocked("source snapshot changed before upload")
            task = task_report(config.repo, started, gateway.url, config.project, previous)
        result["stage"] = "collector"
        sonar = collect(
            client.get,
            config.project,
            config.branch,
            task,
            before["digest"],
            started,
            attempts=config.poll_attempts,
        )
        if snapshot(config.repo) != before:
            raise Blocked("source snapshot changed during scan")
        result["sonar"] = sonar
        result["status"] = "collected"
        export = {"version": 1, "revision": before["digest"], "issues": sonar["issues"]}
        (run / "export.json").write_text(json.dumps(export, indent=2), encoding="utf-8")
        (run / "sonar.json").write_text(json.dumps(sonar, indent=2), encoding="utf-8")
        return result
    except Exception as error:
        # External exception strings may carry command args or HTTP credentials.
        result["reason"] = (
            redact(str(error))
            if isinstance(error, Blocked)
            else type(error).__name__ + ": execution/evidence unavailable"
        )
        if isinstance(error, FailedTests):
            result["status"] = "failed"
        return result
    finally:
        if owned:
            lock.unlink()
        # Redact before retaining reports. Original credentials must not survive in
        # report bodies, even when a test accidentally prints the environment.
        for file in sorted(run.rglob("*")):
            if file.is_file() and not linked(file):
                if file.stat().st_size > 64 * 1024 * 1024:
                    result["status"] = "blocked"
                    result["reason"] = (
                        "report exceeds retention budget; inspect locally before sharing"
                    )
                    continue
                raw = file.read_bytes()
                clean = redact(raw.decode("utf-8", errors="replace")).encode("utf-8")
                file.write_bytes(clean)
                result["reports"][file.relative_to(run).as_posix()] = hashlib.sha256(
                    clean
                ).hexdigest()
        result["finished"] = time.time()
        (run / "evidence.json").write_text(redact(json.dumps(result, indent=2)), encoding="utf-8")


def verify_run(run: str | Path, repo: str | Path, current: bool = True) -> dict[str, Any]:
    run = Path(run).resolve(strict=True)
    evidence = read_json(run / "evidence.json")
    if (
        evidence.get("status") != "collected"
        or not 0 <= time.time() - evidence.get("finished", 0) <= 86400
        or evidence.get("tests", {}).get("total", 0) <= 0
        or evidence.get("tests", {}).get("failed") != 0
        or any(
            evidence.get("return_codes", {}).get(k) != 0
            for k in ("scanner-begin", "build", "tests", "scanner-end")
        )
    ):
        raise Blocked("run unavailable, stale, incomplete or failed")
    reports = evidence.get("reports", {})
    if not {
        "build.log",
        "tests.log",
        "scanner-begin.log",
        "scanner-end.log",
        "sonar.json",
        "export.json",
    } <= set(reports):
        raise Blocked("required report hashes absent")
    for name, expected in reports.items():
        file = safe_path(run, name)
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise Blocked("report hash mismatch")
    retained = {
        p.relative_to(run).as_posix()
        for pattern in ("*.trx", "coverage.opencover.xml")
        for p in (run / "results").rglob(pattern)
    }
    if not retained <= set(reports):
        raise Blocked("test or coverage report hash absent")
    if test_reports(run / "results", evidence["started"]) != evidence["tests"]:
        raise Blocked("retained test counts do not match evidence")
    if read_json(run / "sonar.json") != evidence.get("sonar"):
        raise Blocked("Sonar report/envelope mismatch")
    if current and snapshot(Path(repo).resolve()) != evidence.get("snapshot"):
        raise Blocked("evidence does not bind current source snapshot")
    return evidence


def job_improved(kind: str, issue: str, before: dict[str, Any], after: dict[str, Any]) -> bool:
    # A hotspot/security disposition is never automatically accepted.
    if kind == "security":
        return False
    old = {i["id"] for i in before["issues"]}
    new = {i["id"] for i in after["issues"]}
    return issue in old and issue not in new


def accept_job(
    state: dict[str, Any],
    task: dict[str, Any],
    baseline: str | Path,
    after: str | Path,
    allowed: list[str],
) -> dict[str, Any]:
    if not 1 <= len(set(allowed)) <= 4:
        raise Blocked("approved changed-file budget is 1..4")
    for name in allowed:
        safe_path(state["target"], name)
    before = verify_run(baseline, state["target"], current=False)
    current = verify_run(after, state["target"])
    if before["snapshot"]["digest"] != state["revision"]:
        raise Blocked("baseline does not match planned source snapshot")
    if current["started"] <= before["finished"]:
        raise Blocked("post-change run must follow baseline")
    for key in ("project", "branch"):
        if before["sonar"][key] != current["sonar"][key]:
            raise Blocked("baseline and current Sonar scope differ")
    a, b = before["snapshot"].get("files"), current["snapshot"].get("files")
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise Blocked("file-level identity missing")
    changed = sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))
    if not changed or not set(changed) <= set(allowed):
        raise Blocked("changed files absent or outside approved budget")
    if not job_improved(task["kind"], task["issue"], before["sonar"], current["sonar"]):
        raise Blocked("job lacks proven improvement or requires human security review")
    return {
        "status": "accepted",
        "global_pass": current["sonar"]["global_pass"],
        "changed_files": changed,
        "before": str(baseline),
        "after": str(after),
        "trust": "Local integrity-checked improvement; host must verify behavior/no gaming. Not parent review approval.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", default=str(PACK / ".debt-runs"))
    args = parser.parse_args()
    try:
        result = scan(Config(read_json(args.config)), args.output, args.execute)
        # Never dump report bodies or raw exception/command output to model context.
        print(
            json.dumps(
                {
                    k: v
                    for k, v in result.items()
                    if k in ("status", "stage", "run", "reason", "notice", "stages")
                },
                indent=2,
            )
        )
        return 0 if result["status"] in ("dry-run", "collected") else 2
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": str(error) if isinstance(error, Blocked) else type(error).__name__,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
