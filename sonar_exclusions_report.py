"""Detect Sonar exclusions/suppressions by language, replicating the exclusions report.

Line-level detection of the exclusion/suppression rules a Sonar review flags,
grouped into four categories (direct Sonar exclusions, NOSONAR suppressions,
coverage exclusions, technical exceptions), with a per-language rule prefix.
A project's `.sonarremedy/rules.json` supplies a whitelist (rules to ignore)
and a blacklist (rules to always flag as blocked). Stdlib only.
"""

import argparse
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from debtpack import hidden_secret, linked, relative, safe_path

MAX_VISITED = 20000
MAX_BYTES = 1024 * 1024
MAX_FINDINGS = 500

CAT_SONAR_EXCLUSIONS = "sonar_exclusions"
CAT_SONAR_SUPPRESSIONS = "sonar_suppressions"
CAT_COVERAGE_EXCLUSIONS = "coverage_exclusions"
CAT_TECHNICAL_EXCEPTIONS = "technical_exceptions"

# Rule: (name, category, severity, regex, file suffixes it applies to)
RULES: list[tuple[str, str, str, re.Pattern[str], tuple[str, ...]]] = [
    # Exclusiones directas de Sonar
    (
        "SONAR_EXCLUSIONS",
        CAT_SONAR_EXCLUSIONS,
        "HIGH",
        re.compile(r"sonar\.exclusions\s*="),
        ("sonar-project.properties",),
    ),
    # Supresiones puntuales de Sonar
    (
        "NOSONAR",
        CAT_SONAR_SUPPRESSIONS,
        "MEDIUM",
        re.compile(r"NOSONAR", re.IGNORECASE),
        (
            ".cs",
            ".vb",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            ".java",
            ".kt",
            ".py",
            ".go",
            ".rs",
        ),
    ),
    # Exclusiones de cobertura
    (
        "SONAR_COVERAGE_EXCLUSIONS",
        CAT_COVERAGE_EXCLUSIONS,
        "HIGH",
        re.compile(r"sonar\.coverage\.exclusions\s*="),
        ("sonar-project.properties",),
    ),
    (
        "KARMA_COVERAGE_EXCLUSION",
        CAT_COVERAGE_EXCLUSIONS,
        "HIGH",
        re.compile(r"coverageIstanbulReporter|\bexclude\b"),
        ("karma.conf.js",),
    ),
    (
        "ANGULAR_JSON_COVERAGE_EXCLUSION",
        CAT_COVERAGE_EXCLUSIONS,
        "HIGH",
        re.compile(r"coverage|\bexclude\b"),
        ("angular.json",),
    ),
    (
        "ISTANBUL_IGNORE_NEXT",
        CAT_COVERAGE_EXCLUSIONS,
        "LOW",
        re.compile(r"istanbul\s+ignore\s+next"),
        (".ts", ".tsx", ".js", ".jsx"),
    ),
    (
        "ISTANBUL_IGNORE_FILE",
        CAT_COVERAGE_EXCLUSIONS,
        "LOW",
        re.compile(r"istanbul\s+ignore\s+file"),
        (".ts", ".tsx", ".js", ".jsx"),
    ),
    (
        "EXCLUDE_FROM_CODE_COVERAGE",
        CAT_COVERAGE_EXCLUSIONS,
        "MEDIUM",
        re.compile(r"ExcludeFromCodeCoverage"),
        (".cs", ".vb"),
    ),
    # Excepciones o supresiones técnicas
    (
        "TS_IGNORE",
        CAT_TECHNICAL_EXCEPTIONS,
        "MEDIUM",
        re.compile(r"@ts-(?:ignore|nocheck)"),
        (".ts", ".tsx"),
    ),
    (
        "ESLINT_DISABLE_NEXT_LINE",
        CAT_TECHNICAL_EXCEPTIONS,
        "MEDIUM",
        re.compile(r"eslint-disable-next-line"),
        (".ts", ".tsx", ".js", ".jsx"),
    ),
    (
        "ESLINT_DISABLE_ALL",
        CAT_TECHNICAL_EXCEPTIONS,
        "MEDIUM",
        re.compile(r"eslint-disable(?!-next-line)"),
        (".ts", ".tsx", ".js", ".jsx"),
    ),
    (
        "ESLINT_RULE_DISABLED",
        CAT_TECHNICAL_EXCEPTIONS,
        "HIGH",
        re.compile(r"[\"'][A-Za-z0-9@/_-]+[\"']\s*:\s*(?:[\"']off[\"']|0)"),
        (".eslintrc", ".eslintrc.json", ".eslintrc.js"),
    ),
    (
        "TSLINT_DISABLE_ALL",
        CAT_TECHNICAL_EXCEPTIONS,
        "MEDIUM",
        re.compile(r"tslint:disable"),
        (".ts", ".tsx", ".js", ".jsx"),
    ),
    (
        "SUPPRESS_MESSAGE",
        CAT_TECHNICAL_EXCEPTIONS,
        "MEDIUM",
        re.compile(r"SuppressMessage"),
        (".cs", ".vb"),
    ),
    (
        "PRAGMA_WARNING_DISABLE",
        CAT_TECHNICAL_EXCEPTIONS,
        "MEDIUM",
        re.compile(r"#pragma\s+warning\s+disable"),
        (".cs", ".vb"),
    ),
    ("NOWARN", CAT_TECHNICAL_EXCEPTIONS, "MEDIUM", re.compile(r"<NoWarn>"), (".csproj",)),
    (
        "EDITORCONFIG_SEVERITY_SILENT",
        CAT_TECHNICAL_EXCEPTIONS,
        "LOW",
        re.compile(r"severity\s*=\s*silent"),
        (".editorconfig",),
    ),
]

CATEGORY_NAMES = {
    CAT_SONAR_EXCLUSIONS: "sonar_exclusions",
    CAT_SONAR_SUPPRESSIONS: "sonar_suppressions",
    CAT_COVERAGE_EXCLUSIONS: "coverage_exclusions",
    CAT_TECHNICAL_EXCEPTIONS: "technical_exceptions",
}


class Blocked(ValueError):
    """Scanning capability is unavailable; not a passing result."""


def language_prefix(path: str) -> str:
    """Language tag used as the rule-name prefix (ANGULAR / DOTNET / SONAR / GENERIC)."""
    name = os.path.basename(path).lower()
    ext = os.path.splitext(path)[1].lower()
    if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        return "ANGULAR"
    if name == "angular.json" or name.startswith("karma.conf") or name.startswith(".eslintrc"):
        return "ANGULAR"
    if ext in (".cs", ".vb"):
        return "DOTNET"
    if name.endswith(".csproj") or name == ".editorconfig":
        return "DOTNET"
    if name == "sonar-project.properties":
        return "SONAR"
    return "GENERIC"


def load_rules(target: str | Path) -> dict[str, Any]:
    """Read .sonarremedy/rules.json; default to empty whitelist/blacklist."""
    path = Path(target) / ".sonarremedy" / "rules.json"
    if path.is_file():
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return {
            "whitelist": set(data.get("whitelist", [])),
            "blacklist": set(data.get("blacklist", [])),
        }
    return {"whitelist": set(), "blacklist": set()}


def rules_path(target: str | Path) -> Path:
    return Path(target) / ".sonarremedy" / "rules.json"


def _read_rules_file(target: str | Path) -> dict[str, list[str]]:
    path = rules_path(target)
    if path.is_file():
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return {
            "whitelist": sorted(set(data.get("whitelist", []))),
            "blacklist": sorted(set(data.get("blacklist", []))),
        }
    return {"whitelist": [], "blacklist": []}


def _write_rules_file(target: str | Path, data: dict[str, list[str]]) -> Path:
    path = rules_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def list_rules(target: str | Path) -> dict[str, Any]:
    data = _read_rules_file(target)
    return {"status": "ok", "whitelist": data["whitelist"], "blacklist": data["blacklist"]}


def add_rule(target: str | Path, rule: str, list_name: str) -> dict[str, Any]:
    data = _read_rules_file(target)
    data[list_name] = sorted(set(data[list_name]) | {rule})
    _write_rules_file(target, data)
    return {"status": "ok", "whitelist": data["whitelist"], "blacklist": data["blacklist"]}


def remove_rule(target: str | Path, rule: str) -> dict[str, Any]:
    data = _read_rules_file(target)
    removed = []
    for list_name in ("whitelist", "blacklist"):
        if rule in data[list_name]:
            data[list_name].remove(rule)
            removed.append(list_name)
    _write_rules_file(target, data)
    return {
        "status": "ok",
        "removed_from": removed,
        "whitelist": data["whitelist"],
        "blacklist": data["blacklist"],
    }


def scan(repo: str | Path, *, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        root = Path(repo).resolve(strict=True)
    except OSError:
        raise Blocked("repo must be an existing directory") from None
    if not root.is_dir():
        raise Blocked("repo must be an existing directory")
    config = rules or load_rules(root)
    findings: list[dict[str, Any]] = []
    files_scanned = 0
    visited = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        visited += 1
        if visited > MAX_VISITED:
            raise Blocked("discovery limit exceeded; choose a smaller target")
        dirs[:] = sorted(x for x in dirs if not hidden_secret(x) and not linked(Path(base) / x))
        for name in sorted(files):
            visited += 1
            if visited > MAX_VISITED:
                raise Blocked("discovery limit exceeded; choose a smaller target")
            path = Path(base) / name
            if hidden_secret(name) or linked(path):
                continue
            rel = path.relative_to(root).as_posix()
            try:
                relative(rel)
                safe_path(root, rel)
            except ValueError:
                continue
            try:
                if path.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
                continue
            applicable = [rule for rule in RULES if name.endswith(rule[4])]
            if not applicable:
                continue
            files_scanned += 1
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for line_no, line in enumerate(handle, 1):
                        for rule_name, category, severity, regex, _ in applicable:
                            match = regex.search(line)
                            if not match:
                                continue
                            findings.append(
                                {
                                    "rule": f"{language_prefix(name)}.{rule_name}",
                                    "file": rel,
                                    "line": line_no,
                                    "severity": severity,
                                    "category": category,
                                    "evidence": match.group(0)[:80],
                                }
                            )
                            if len(findings) >= MAX_FINDINGS:
                                break
                        if len(findings) >= MAX_FINDINGS:
                            break
            except OSError:
                continue
            if len(findings) >= MAX_FINDINGS:
                break
        if len(findings) >= MAX_FINDINGS:
            break

    # Apply whitelist (skip) / blacklist (block).
    filtered = []
    for finding in findings:
        rule = finding["rule"]
        if rule in config["whitelist"]:
            continue
        finding["status"] = "blocked" if rule in config["blacklist"] else "pending"
        filtered.append(finding)

    counts: dict[str, int] = dict.fromkeys(CATEGORY_NAMES, 0)
    for finding in filtered:
        counts[finding["category"]] = counts.get(finding["category"], 0) + 1
    counts["total"] = len(filtered)
    blocked = sum(1 for f in filtered if f["status"] == "blocked")
    return {
        "status": "scanned",
        "files_scanned": files_scanned,
        "counts": counts,
        "findings": filtered,
        "notice": (
            f"{len(filtered)} exclusion(s) detected ({blocked} blocked by blacklist)"
            if filtered
            else "no exclusions detected"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="absolute path to the repository to scan")
    parser.add_argument("--output", help="optional JSON report path outside the repo")
    args = parser.parse_args()
    try:
        result = scan(args.repo)
        payload = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
        if args.output:
            output = Path(args.output).resolve()
            root = Path(args.repo).resolve(strict=True)
            if output.is_relative_to(root):
                raise Blocked("report output must be outside the scanned repository")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload + "\n", encoding="utf-8")
        with contextlib.suppress(AttributeError, ValueError):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(payload)
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
