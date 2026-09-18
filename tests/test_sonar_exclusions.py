"""Offline fixtures only: no real repository, credentials or network required."""
import contextlib
import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sonar_exclusions as e

PACK = Path(__file__).resolve().parents[1]


class ExclusionScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=PACK / '.debt-state')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        (self.repo / 'src').mkdir(parents=True)

    def write(self, rel, content):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def test_counts_exact_mixed_fixture(self):
        self.write('src/A.cs', '\n'.join([
            '// fixture source',
            '#pragma warning disable S2094',
            '[SuppressMessage("Major Code Smell", "S2094:Classes should not be empty", Justification = "fixture")]',
            '[ExcludeFromCodeCoverage]',
            'public class A { } // NOSONAR',
            '']))
        self.write('src/B.cs', '// NOSONAR:S2094\n')
        self.write('src/app/app.ts', 'const x = 1; // NOSONAR\n')
        self.write('src/App.csproj', '<Project><PropertyGroup><SonarQubeExclude>true</SonarQubeExclude>'
                                     '</PropertyGroup></Project>\n')
        self.write('sonar-project.properties', '\n'.join([
            'sonar.projectKey=fixture',
            'sonar.exclusions=src/generated/**',
            'sonar.coverage.exclusions=src/test/**',
            'sonar.cpd.exclusions=src/legacy/**',
            'sonar.issue.ignore.multicriteria.1.ruleKey=csharpsquid:S2094',
            '']))
        result = e.scan(self.repo)
        self.assertEqual(result['status'], 'scanned')
        self.assertEqual(result['files_scanned'], 5)
        self.assertEqual(result['exclusions'], {
            'nosonar': 3,
            'nosonar_rule_specific': 1,
            'pragma_suppress': 1,
            'suppress_message': 1,
            'exclude_from_coverage': 1,
            'sonarqube_exclude': 1,
            'properties_exclusions': {
                'sonar_exclusions': 1,
                'sonar_coverage_exclusions': 1,
                'sonar_cpd_exclusions': 1,
                'sonar_issue_ignore_multicriteria': 1,
            }})
        self.assertEqual(result['files_with_exclusions'], 5)
        self.assertEqual(result['notice'], 'exclusions detected; metrics may be gamed — review before accepting')

    def test_clean_repo_reports_no_exclusions(self):
        self.write('src/A.cs', 'public class A { }\n')
        result = e.scan(self.repo)
        self.assertEqual(result['files_scanned'], 1)
        self.assertEqual(result['files_with_exclusions'], 0)
        self.assertEqual(result['exclusions']['nosonar'], 0)
        self.assertEqual(result['notice'], 'no metric-gaming exclusions detected')

    def test_ignored_directories_and_secrets_never_scanned(self):
        self.write('bin/C.cs', '// NOSONAR\n')
        self.write('obj/C.cs', '// NOSONAR\n')
        self.write('node_modules/pkg/x.ts', '// NOSONAR\n')
        self.write('src/token.txt', 'secret-value-fixture\n')
        self.write('src/.env', 'SECRET=fixture\n')
        self.write('src/A.cs', 'public class A { }\n')
        result = e.scan(self.repo)
        self.assertEqual(result['files_scanned'], 1)
        self.assertEqual(result['files_with_exclusions'], 0)
        self.assertEqual(result['exclusions']['nosonar'], 0)
        self.assertNotIn('secret-value-fixture', json.dumps(result))
        self.assertNotIn('SECRET', json.dumps(result))

    def test_csproj_and_properties_counted_per_file_once(self):
        self.write('src/App.csproj', '<SonarQubeExclude>true</SonarQubeExclude>\n'
                                     '<SonarQubeExclude>true</SonarQubeExclude>\n')
        self.write('sonar-project.properties', 'sonar.exclusions=a\nsonar.exclusions=b\n')
        result = e.scan(self.repo)
        self.assertEqual(result['exclusions']['sonarqube_exclude'], 1)
        self.assertEqual(result['exclusions']['properties_exclusions']['sonar_exclusions'], 1)
        self.assertEqual(result['files_with_exclusions'], 2)

    def test_file_limit_exceeded_blocks(self):
        for i in range(30):
            self.write('src/f%02d.cs' % i, '// NOSONAR\n')
        with patch.object(e, 'MAX_VISITED', 10), self.assertRaises(e.Blocked):
            e.scan(self.repo)

    def test_oversized_file_skipped(self):
        self.write('src/Big.cs', 'public class B { } // NOSONAR\n')
        with patch.object(e, 'MAX_BYTES', 1):
            result = e.scan(self.repo)
        self.assertEqual(result['exclusions']['nosonar'], 0)
        self.assertEqual(result['files_scanned'], 0)

    def test_invalid_repo_blocks(self):
        with self.assertRaises(e.Blocked):
            e.scan(self.root / 'missing')
        self.write('src/A.cs', 'x')
        with self.assertRaises(e.Blocked):
            e.scan(self.repo / 'src' / 'A.cs')

    def test_main_exit_codes_and_output(self):
        self.write('src/A.cs', '// NOSONAR\n')
        report = self.root / 'report.json'
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                patch.object(sys, 'argv', ['sonar_exclusions.py', str(self.repo), '--output', str(report)]):
            self.assertEqual(e.main(), 0)
        written = json.loads(report.read_text(encoding='utf-8'))
        printed = json.loads(out.getvalue())
        self.assertEqual(written, printed)
        self.assertEqual(printed['status'], 'scanned')
        self.assertEqual(printed['exclusions']['nosonar'], 1)
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                patch.object(sys, 'argv', ['sonar_exclusions.py', str(self.root / 'missing')]):
            self.assertEqual(e.main(), 2)
        self.assertEqual(json.loads(out.getvalue())['status'], 'blocked')
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                patch.object(sys, 'argv', ['sonar_exclusions.py', str(self.repo),
                                           '--output', str(self.repo / 'report.json')]):
            self.assertEqual(e.main(), 2)


if __name__ == '__main__':
    unittest.main()