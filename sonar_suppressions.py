"""Detect suppression directives that may evade Sonar analysis. Stdlib only.

Line-level detection with a certainty tier per finding:

- ``certain``: unambiguous Sonar/compiler/linter suppression directives — no AI.
- ``ambiguous``: candidates the AI must judge (generic tokens with other meanings,
  or commonly-legitimate suppressions). Mechanical detection runs first, so no
  tokens are spent unless a finding is ambiguous.

The pack never calls the AI; it only surfaces the ambiguous findings (bounded)
so the orchestrator/worker can judge them.
"""

import argparse
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import typesafe_client
from debtpack import hidden_secret, linked, relative, safe_path

MAX_VISITED = 20000
MAX_BYTES = 1024 * 1024
MAX_FINDINGS = 200
# Advisory TypeSafe legitimacy scoring: bounded even when TYPESAFE_API_KEY is
# set, so a large findings list never turns a scan into hundreds of API calls.
MAX_SCORED = 20
# No per-finding category field exists in this module's findings (unlike
# sonar_exclusions_report.py); every finding here is a suppression directive.
_LEGITIMACY_CATEGORY = "sonar_suppressions"

# Each entry: (regex, kind, certainty). Certain patterns are listed before the
# ambiguous ones, so a line with both "NOSONAR:rule" and "NOSONAR" resolves to
# the specific (certain) form first.
PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"NOSONAR\s*:\s*[A-Za-z0-9_,.\-]+", re.IGNORECASE),
        "nosonar_rule_specific",
        "certain",
    ),
    (
        re.compile(r"#pragma\s+warning\s+disable", re.IGNORECASE),
        "pragma_warning_disable",
        "certain",
    ),
    (re.compile(r"\[SuppressMessage\s*\(", re.IGNORECASE), "suppress_message", "certain"),
    (re.compile(r"ExcludeFromCodeCoverage", re.IGNORECASE), "exclude_from_coverage", "certain"),
    (re.compile(r"@SuppressWarnings\s*\("), "suppress_warnings", "certain"),
    (re.compile(r"@ts-(?:ignore|nocheck)"), "ts_suppression", "certain"),
    (re.compile(r"eslint-disable"), "eslint_disable", "certain"),
    (re.compile(r"tslint:disable"), "tslint_disable", "certain"),
    (re.compile(r"#\[allow\s*\("), "rust_allow", "certain"),
    # ambiguous — the AI must judge whether these truly evade Sonar
    (re.compile(r"#\s*noqa"), "noqa", "ambiguous"),
    (re.compile(r"#\s*type:\s*ignore"), "type_ignore", "ambiguous"),
    (re.compile(r"#\s*pylint:\s*disable"), "pylint_disable", "ambiguous"),
    (re.compile(r"#\s*nosec\b"), "nosec", "ambiguous"),
    (re.compile(r"#pragma(?!\s+warning\s+disable)", re.IGNORECASE), "pragma_generic", "ambiguous"),
    (re.compile(r"NOSONAR", re.IGNORECASE), "nosonar_bare", "ambiguous"),
]

CODE_EXTENSIONS = (
    ".py",
    ".cs",
    ".vb",
    ".java",
    ".kt",
    ".kts",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hpp",
    ".cshtml",
    ".razor",
)


class Blocked(ValueError):
    """Scanning capability is unavailable; not a passing result."""


def _scan_line(line: str) -> tuple[str, str, str] | None:
    """Return (kind, certainty, match) for the first suppression pattern on a line."""
    for pattern, kind, certainty in PATTERNS:
        match = pattern.search(line)
        if match:
            return kind, certainty, match.group(0)
    return None


def scan(repo: str | Path) -> dict[str, Any]:
    try:
        root = Path(repo).resolve(strict=True)
    except OSError:
        raise Blocked("repo must be an existing directory") from None
    if not root.is_dir():
        raise Blocked("repo must be an existing directory")
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
            if hidden_secret(name) or linked(path) or not name.endswith(CODE_EXTENSIONS):
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
            files_scanned += 1
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for line_no, line in enumerate(handle, 1):
                        hit = _scan_line(line)
                        if hit is None:
                            continue
                        kind, certainty, match = hit
                        findings.append(
                            {
                                "file": rel,
                                "line": line_no,
                                "kind": kind,
                                "certainty": certainty,
                                "match": match[:80],
                            }
                        )
                        if len(findings) >= MAX_FINDINGS:
                            break
            except OSError:
                continue
            if len(findings) >= MAX_FINDINGS:
                break
        if len(findings) >= MAX_FINDINGS:
            break
    truncated = len(findings) >= MAX_FINDINGS

    # Advisory-only TypeSafe legitimacy triage: opt-in via TYPESAFE_API_KEY,
    # bounded to MAX_SCORED calls, only for "ambiguous" findings (the ones
    # this module already says need AI judgment) -- "certain" findings are an
    # unambiguous, settled detection with nothing for TypeSafe to adjudicate.
    # Zero calls and zero output change when the key is unset.
    if os.environ.get("TYPESAFE_API_KEY"):
        ambiguous_findings = [f for f in findings if f["certainty"] == "ambiguous"]
        for finding in ambiguous_findings[:MAX_SCORED]:
            finding["typesafe_legitimacy"] = typesafe_client.legitimacy_score(
                finding["kind"], _LEGITIMACY_CATEGORY, finding["file"], finding["match"]
            )

    certain = sum(1 for f in findings if f["certainty"] == "certain")
    ambiguous = sum(1 for f in findings if f["certainty"] == "ambiguous")
    notice = (
        f"{ambiguous} ambiguous suppression(s) need AI judgment"
        if ambiguous
        else "no ambiguous suppressions detected"
    )
    if truncated:
        notice += f" (stopped early at the {MAX_FINDINGS}-finding cap, more may exist)"
    return {
        "status": "scanned",
        "files_scanned": files_scanned,
        "findings": findings,
        "counts": {"certain": certain, "ambiguous": ambiguous, "total": len(findings)},
        "truncated": truncated,
        "notice": notice,
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
