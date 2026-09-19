"""API→export bridge: plan Sonar debt for any stack already analyzed. No scan, no manual export."""

import argparse
import json
import math
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

from debtpack import MAX_ISSUES, PACK, read_json, token
from sonar_client import Blocked, Client, endpoint, normalize, pages, pages_all
from sonar_local import child_environment, redactor

SEVERITIES = ("BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO")


class Config:
    def __init__(self, raw: dict[str, Any]) -> None:
        allowed = {
            "adapter",
            "repo",
            "url",
            "trusted_url",
            "project",
            "branch",
            "timeout",
            "severities",
            "exclude_ids",
            "allow_http",
        }
        required = ("adapter", "repo", "url", "trusted_url", "project", "branch")
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise Blocked(
                "unsupported configuration field; credentials and skip switches forbidden"
            )
        if any(key not in raw for key in required):
            raise Blocked("missing required configuration field")
        if raw.get("adapter") != "api":
            raise Blocked("only api adapter implemented; other adapters explicitly unsupported")
        self.allow_http = raw.get("allow_http", False)
        if type(self.allow_http) is not bool:
            raise Blocked("allow_http must be a boolean")
        self.url = endpoint(raw["url"], allow_http=self.allow_http)
        if endpoint(raw["trusted_url"], allow_http=self.allow_http) != self.url:
            raise Blocked("trusted_url must explicitly match the final server URL")
        try:
            self.repo = Path(raw["repo"]).resolve(strict=True)
        except FileNotFoundError:
            raise Blocked("repo must be an existing directory") from None
        if not self.repo.is_dir():
            raise Blocked("repo must be an existing directory")
        self.project, self.branch = raw["project"], raw["branch"]
        for name in ("project", "branch"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", value):
                raise Blocked("invalid project or branch")
        self.timeout = raw.get("timeout", 60)
        if type(self.timeout) is not int or not 1 <= self.timeout <= 3600:
            raise Blocked("timeout must be 1..3600 seconds")
        self.severities = self._severities(raw.get("severities"))
        self.exclude_ids = self._exclude_ids(raw.get("exclude_ids"))

    @staticmethod
    def _severities(value: str | None) -> str | None:
        # Optional scope filter: lets an owner preselect a bounded severity
        # slice (e.g. BLOCKER,CRITICAL) when a project exceeds the 2,000-issue
        # export budget. Absent by default; never widens what the API returns.
        if value is None:
            return None
        if not isinstance(value, str) or value == "":
            raise Blocked("severities must be a non-empty comma-separated string")
        parts = value.split(",")
        if len(parts) != len(set(parts)) or any(part not in SEVERITIES for part in parts):
            raise Blocked("severities must list unique known values: " + ",".join(SEVERITIES))
        return value

    @staticmethod
    def _exclude_ids(value: list[str] | None) -> frozenset[str] | None:
        # Optional, owner-reviewed skip-list for individually untriaged
        # findings (e.g. a rule with no usable line); never a bulk suppression.
        if value is None:
            return None
        if (
            not isinstance(value, list)
            or not value
            or len(value) != len(set(value))
            or any(not token(item) for item in value)
        ):
            raise Blocked("exclude_ids must be a non-empty list of unique safe tokens")
        return frozenset(value)


def git_head(repo: Path) -> str:
    def git(*args: str) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=30,
            env=child_environment(),
            check=False,
        )
        if result.returncode:
            raise Blocked("Git identity unavailable; repository needs a valid HEAD")
        if len(result.stdout) > 64 * 1024:
            raise Blocked("Git evidence budget exceeded")
        return result.stdout

    root = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != repo.resolve():
        raise Blocked("repo must be the Git root")
    revision = git("rev-parse", "HEAD").decode().strip()
    if not token(revision):
        raise Blocked("invalid HEAD revision")
    return revision


def git_dirty(repo: Path) -> bool:
    """Non-blocking advisory: uncommitted edits are not part of the analyzed revision."""
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        timeout=30,
        env=child_environment(),
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def measures(get: Callable[..., Any], project: str, branch: str) -> dict[str, float]:
    keys = (
        "coverage",
        "duplicated_lines_density",
        "code_smells",
        "vulnerabilities",
        "security_hotspots",
        "security_hotspots_reviewed",
    )
    component = get(
        "api/measures/component", component=project, branch=branch, metricKeys=",".join(keys)
    ).get("component", {})
    if component.get("key") != project:
        raise Blocked("metrics project mismatch")
    found = {}
    for measure in component.get("measures", []):
        key = measure.get("metric")
        if key not in keys or key in found:
            raise Blocked("unexpected or duplicate metric")
        try:
            value = float(measure["value"])
        except (KeyError, ValueError, TypeError):
            raise Blocked("metric has no numeric value") from None
        if not math.isfinite(value) or value < 0:
            raise Blocked("invalid metric value")
        if key in ("coverage", "duplicated_lines_density", "security_hotspots_reviewed"):
            if value > 100:
                raise Blocked("percentage out of range")
        elif not value.is_integer():
            raise Blocked("count must be an integer")
        found[key] = value
    # Sonar omits this percentage when there are explicitly no hotspots.
    if found.get("security_hotspots") == 0:
        found.setdefault("security_hotspots_reviewed", 100.0)
    if set(found) != set(keys):
        raise Blocked("required metric missing; not zero")
    return found


def chunk_issues(normalized: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [normalized[i : i + chunk_size] for i in range(0, len(normalized), chunk_size)]


def fetch(config: Config, output: str | Path) -> dict[str, Any]:
    secret = os.environ.get("SONAR_TOKEN", "")
    if not secret or secret.strip() != secret or any(ord(x) < 33 for x in secret):
        raise Blocked("SONAR_TOKEN must be present in the local process environment")
    output = Path(output).resolve()
    if output.is_relative_to(config.repo):
        raise Blocked("run output must be outside target repo to keep snapshot stable")
    output.mkdir(parents=True, exist_ok=True)
    client = Client(config.url, secret, config.timeout, allow_http=config.allow_http)
    get = client.get
    # Revision binding is degradable: a token that cannot read the analyses
    # endpoint (HTTP 403) may still read issues/measures/gate/rules. In that
    # case the export binds the local HEAD and reports analysis_warning.
    # A real 403 surfaces as HTTPError (urllib), so both are caught here.
    analysis_warning = None
    revision = None
    try:
        data = get(
            "api/project_analyses/search", project=config.project, branch=config.branch, ps=1
        )
        entries = data.get("analyses", [])
        if entries:
            candidate = entries[0].get("revision")
            if token(candidate):
                revision = candidate
            else:
                analysis_warning = "analysis revision missing or invalid; using local HEAD"
        else:
            analysis_warning = "no branch analysis found; using local HEAD as revision"
    except (Blocked, HTTPError) as error:
        analysis_warning = f"analysis lookup blocked ({str(error)}); using local HEAD as revision"
    head = git_head(config.repo)
    if revision is None:
        revision = head
        if analysis_warning is None:
            analysis_warning = "no analysis revision available; using local HEAD"
    elif head != revision:
        raise Blocked(
            f"checkout must be at the analyzed revision; HEAD {head[:12]} does not match analysis {revision[:12]}"
        )
    gate = (
        get("api/qualitygates/project_status", projectKey=config.project, branch=config.branch)
        .get("projectStatus", {})
        .get("status")
    )
    if gate not in ("OK", "ERROR"):
        raise Blocked("quality gate unavailable")
    found = measures(get, config.project, config.branch)
    issue_params = {"componentKeys": config.project, "branch": config.branch, "resolved": "false"}
    if config.severities is not None:
        issue_params["severities"] = config.severities
    issues = pages_all(get, "api/issues/search", "issues", issue_params)
    excluded_count = 0
    if config.exclude_ids is not None:
        before = len(issues)
        issues = [item for item in issues if item.get("key") not in config.exclude_ids]
        excluded_count = before - len(issues)
    # Hotspot lookup is degradable too: a token that cannot read the hotspots
    # endpoint (HTTP 403) may still fetch issues/measures/gate/rules. In that
    # case unreviewed_hotspots is unknown (None) and hotspots_warning is set;
    # the export then contains issues only.
    hotspots_warning = None
    try:
        hotspots = pages(
            get,
            "api/hotspots/search",
            "hotspots",
            {"projectKey": config.project, "branch": config.branch},
        )
    except (Blocked, HTTPError) as error:
        hotspots_warning = f"hotspot lookup blocked ({str(error)}); unreviewed hotspots unknown"
        hotspots = []
    normalized = []
    deferred_no_line = 0
    for item in issues:
        line = item.get("line")
        if type(line) is not int or not 1 <= line <= 10000000:
            deferred_no_line += 1
            continue
        kind = {"CODE_SMELL": "smells", "VULNERABILITY": "security", "BUG": "smells"}.get(
            item.get("type")
        )
        if not kind:
            raise Blocked("unsupported issue taxonomy; explicit adapter update required")
        normalized.append(normalize(item, config.project, kind))
    unreviewed = None if hotspots_warning is not None else 0
    for item in hotspots:
        status, resolution = item.get("status"), item.get("resolution")
        if status == "TO_REVIEW":
            unreviewed += 1
            line = item.get("line")
            if type(line) is int and 1 <= line <= 10000000:
                normalized.append(normalize(item, config.project, "security"))
            else:
                deferred_no_line += 1
        elif status != "REVIEWED" or resolution not in ("SAFE", "FIXED"):
            raise Blocked("hotspot disposition unresolved or unsupported")
    chunks = chunk_issues(normalized, MAX_ISSUES) or [[]]
    export_paths = []
    for idx, chunk in enumerate(chunks):
        path = (output / "export.json") if len(chunks) == 1 else (output / f"chunk-{idx}.json")
        path.write_text(
            json.dumps({"version": 1, "revision": revision, "issues": chunk}, indent=2),
            encoding="utf-8",
        )
        export_paths.append(path)
    export_path = export_paths[0]
    # On-demand rule detail: compact rules.json for the unique rules of this export.
    # Never dump full descriptions into the result; workers read the file if needed.
    rules_path, rules_count, rules_warning = None, 0, None
    rules = sorted({issue["rule"] for issue in normalized})
    if rules:
        try:
            matched = {}
            for start in range(0, len(rules), 50):
                batch = rules[start : start + 50]
                data = get("api/rules/search", rule_keys=",".join(batch), ps=50)
                for rule in data.get("rules", []):
                    key = rule.get("key")
                    if not isinstance(key, str) or not key:
                        continue
                    entry = {"key": key}
                    if isinstance(rule.get("name"), str):
                        entry["name"] = rule["name"]
                    if isinstance(rule.get("description"), str):
                        entry["description"] = rule["description"][:200]
                    if isinstance(rule.get("remediation"), dict):
                        entry["remediation"] = rule["remediation"]
                    matched[key] = entry
            payload = {key: matched[key] for key in rules if key in matched}
            rules_path = output / "rules.json"
            rules_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            rules_count = len(payload)
        except Exception as error:
            rules_warning = "rules lookup failed: " + redactor(secret)(str(error))
    kinds = {}
    for issue in normalized:
        kinds[issue["kind"]] = kinds.get(issue["kind"], 0) + 1
    warnings = []
    if excluded_count:
        warnings.append(f"excluded {excluded_count} explicitly configured issue(s)")
    if deferred_no_line:
        warnings.append(
            f"deferred {deferred_no_line} issue(s) without file/line (manual triage required)"
        )
    if git_dirty(config.repo):
        warnings.append(
            "working tree is not clean; export binds the analyzed revision, not uncommitted edits"
        )
    # Compact only: never leak issue lists or raw evidence into model context.
    result = {
        "status": "collected",
        "export": str(export_path),
        "project": config.project,
        "branch": config.branch,
        "revision": revision,
        "gate": gate,
        "exports": [str(p) for p in export_paths],
        "chunks": len(chunks),
        "measures": {
            "coverage": found["coverage"],
            "duplication": found["duplicated_lines_density"],
            "smells": found["code_smells"],
            "vulnerabilities": found["vulnerabilities"],
            "hotspots": found["security_hotspots"],
        },
        "issues_total": len(normalized),
        "issues_by_kind": kinds,
        "deferred_no_line": deferred_no_line,
        "unreviewed_hotspots": unreviewed,
        "warnings": warnings,
    }
    if rules_path is not None:
        result["rules"] = str(rules_path)
        result["rules_count"] = rules_count
    if rules_warning is not None:
        result["rules_warning"] = rules_warning
    if analysis_warning is not None:
        result["analysis_warning"] = analysis_warning
    if hotspots_warning is not None:
        result["hotspots_warning"] = hotspots_warning
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--output", default=str(PACK / ".debt-runs"))
    args = parser.parse_args()
    try:
        result = fetch(Config(read_json(args.config)), args.output)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "collected" else 2
    except Exception as error:
        secret = os.environ.get("SONAR_TOKEN", "")
        reason = (
            redactor(secret)(str(error)) if isinstance(error, Blocked) else type(error).__name__
        )
        print(json.dumps({"status": "blocked", "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
