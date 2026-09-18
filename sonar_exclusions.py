"""Offline Sonar exclusion scan: count metric-gaming suppression patterns. Stdlib only.

Detects and counts (never dumps) code-level exclusions that can game Sonar
metrics: `// NOSONAR`, `#pragma warning disable`, `[SuppressMessage]`,
`[ExcludeFromCodeCoverage]`, `<SonarQubeExclude>` in `.csproj`, and exclusion
keys in `sonar-project.properties`. Output is one compact JSON object with
counts only; the orchestrator must never read full file lists or patterns.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from debtpack import hidden_secret, linked, relative, safe_path

MAX_VISITED = 20000
MAX_BYTES = 1024 * 1024
CODE_EXTENSIONS = ('.cs', '.vb', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs',
                   '.cshtml', '.razor')
PROPERTIES_NAME = 'sonar-project.properties'
CSPROJ_SUFFIX = '.csproj'
PROPERTY_KEYS = ('sonar.exclusions', 'sonar.coverage.exclusions',
                 'sonar.cpd.exclusions', 'sonar.issue.ignore.multicriteria')
PROPERTY_FIELDS = {'sonar.exclusions': 'sonar_exclusions',
                   'sonar.coverage.exclusions': 'sonar_coverage_exclusions',
                   'sonar.cpd.exclusions': 'sonar_cpd_exclusions',
                   'sonar.issue.ignore.multicriteria': 'sonar_issue_ignore_multicriteria'}
NOTICE_ALERT = 'exclusions detected; metrics may be gamed — review before accepting'
NOTICE_CLEAN = 'no metric-gaming exclusions detected'

NOSONAR = re.compile(r'NOSONAR', re.IGNORECASE)
NOSONAR_RULES = re.compile(r'NOSONAR\s*:\s*[A-Za-z0-9_,.\-]+', re.IGNORECASE)
PRAGMA = re.compile(r'#pragma\s+warning\s+disable')
SUPPRESS = re.compile(r'SuppressMessage')
COVERAGE = re.compile(r'ExcludeFromCodeCoverage')
SONARQUBE_EXCLUDE = re.compile(r'<SonarQubeExclude>\s*true\s*</SonarQubeExclude>', re.IGNORECASE)


class Blocked(ValueError):
    """Scanning capability is unavailable; not a passing result."""


def empty_counts():
    return dict(nosonar=0, nosonar_rule_specific=0, pragma_suppress=0,
                suppress_message=0, exclude_from_coverage=0, sonarqube_exclude=0,
                properties_exclusions=dict(sonar_exclusions=0, sonar_coverage_exclusions=0,
                                           sonar_cpd_exclusions=0,
                                           sonar_issue_ignore_multicriteria=0))


def scan_source(path, counts):
    """Count line-based suppression patterns in one source file.

    Returns True/False for scanned files, None when the file is not scanned
    (oversized or unreadable).
    """
    hit = False
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                if NOSONAR.search(line):
                    counts['nosonar'] += 1
                    hit = True
                    if NOSONAR_RULES.search(line):
                        counts['nosonar_rule_specific'] += 1
                if PRAGMA.search(line):
                    counts['pragma_suppress'] += 1
                    hit = True
                if SUPPRESS.search(line):
                    counts['suppress_message'] += 1
                    hit = True
                if COVERAGE.search(line):
                    counts['exclude_from_coverage'] += 1
                    hit = True
    except OSError:
        return None
    return hit


def scan_csproj(path, counts):
    """Count a .csproj file once if it contains <SonarQubeExclude>true</...>.

    Returns True/False for scanned files, None when not scanned.
    """
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                if SONARQUBE_EXCLUDE.search(line):
                    counts['sonarqube_exclude'] += 1
                    return True
    except OSError:
        return None
    return False


def scan_properties(path, counts):
    """Count each exclusion key once per sonar-project.properties file.

    Returns True/False for scanned files, None when not scanned.
    """
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        present = set()
        hit = False
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                for key in PROPERTY_KEYS:
                    if key in present:
                        continue
                    if re.match(r'^' + re.escape(key) + r'(?:\.|\s*=)', line):
                        counts['properties_exclusions'][PROPERTY_FIELDS[key]] += 1
                        present.add(key)
                        hit = True
    except OSError:
        return None
    return hit


def scan(repo):
    try:
        root = Path(repo).resolve(strict=True)
    except OSError:
        raise Blocked('repo must be an existing directory') from None
    if not root.is_dir():
        raise Blocked('repo must be an existing directory')
    counts = empty_counts()
    files_scanned = 0
    files_with_exclusions = 0
    visited = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        visited += 1
        if visited > MAX_VISITED:
            raise Blocked('discovery limit exceeded; choose a smaller target')
        dirs[:] = sorted(x for x in dirs if not hidden_secret(x) and not linked(Path(base) / x))
        for name in sorted(files):
            visited += 1
            if visited > MAX_VISITED:
                raise Blocked('discovery limit exceeded; choose a smaller target')
            path = Path(base) / name
            if hidden_secret(name) or linked(path):
                continue
            rel = path.relative_to(root).as_posix()
            try:
                relative(rel)
                safe_path(root, rel)
            except ValueError:
                continue
            if name == PROPERTIES_NAME:
                hit = scan_properties(path, counts)
            elif name.endswith(CSPROJ_SUFFIX):
                hit = scan_csproj(path, counts)
            elif name.endswith(CODE_EXTENSIONS):
                hit = scan_source(path, counts)
            else:
                continue
            if hit is None:
                continue
            files_scanned += 1
            if hit:
                files_with_exclusions += 1
    return {'status': 'scanned', 'files_scanned': files_scanned,
            'exclusions': counts, 'files_with_exclusions': files_with_exclusions,
            'notice': NOTICE_ALERT if files_with_exclusions else NOTICE_CLEAN}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', help='absolute path to the repository to scan')
    parser.add_argument('--output', help='optional JSON report path outside the repo; '
                                         'default prints to stdout only')
    args = parser.parse_args()
    try:
        result = scan(args.repo)
        payload = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
        if args.output:
            output = Path(args.output).resolve()
            root = Path(args.repo).resolve(strict=True)
            if output.is_relative_to(root):
                raise Blocked('report output must be outside the scanned repository')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload + '\n', encoding='utf-8')
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
        print(payload)
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())