"""Tests for the exclusions report scanner (language + categories + rules)."""

import tempfile
import unittest
from pathlib import Path

import sonar_exclusions_report as report


class ExclusionsReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel: str, content: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def scan(self, **kwargs):
        return report.scan(self.root, **kwargs)

    def test_detects_nosonar_and_pragma(self):
        self.write("src/a.cs", "class A { } // NOSONAR\n#pragma warning disable CS0168\n")
        rules = {f["rule"] for f in self.scan()["findings"]}
        self.assertIn("DOTNET.NOSONAR", rules)
        self.assertIn("DOTNET.PRAGMA_WARNING_DISABLE", rules)

    def test_detects_ts_ignore_as_angular_technical(self):
        self.write("src/a.ts", "// @ts-ignore\nconst x = 1;\n")
        finding = self.scan()["findings"][0]
        self.assertEqual(finding["rule"], "ANGULAR.TS_IGNORE")
        self.assertEqual(finding["category"], "technical_exceptions")

    def test_detects_sonar_exclusions_in_properties(self):
        self.write("sonar-project.properties", "sonar.exclusions=**/*.spec.ts\n")
        self.assertIn("SONAR.SONAR_EXCLUSIONS", {f["rule"] for f in self.scan()["findings"]})

    def test_reports_line_and_severity(self):
        self.write("src/a.ts", "const a = 1;\n// @ts-ignore\nconst b = 2;\n")
        finding = next(f for f in self.scan()["findings"] if f["rule"] == "ANGULAR.TS_IGNORE")
        self.assertEqual(finding["line"], 2)
        self.assertEqual(finding["severity"], "MEDIUM")

    def test_categories_are_counted(self):
        self.write("sonar-project.properties", "sonar.exclusions=x\n")
        self.write("src/a.cs", "// NOSONAR\n")
        self.write("src/a.ts", "// @ts-ignore\n")
        self.write("src/b.cs", "[ExcludeFromCodeCoverage]\n")
        counts = self.scan()["counts"]
        self.assertEqual(counts["sonar_exclusions"], 1)
        self.assertEqual(counts["sonar_suppressions"], 1)
        self.assertEqual(counts["coverage_exclusions"], 1)
        self.assertEqual(counts["technical_exceptions"], 1)

    def test_whitelist_skips_and_blacklist_blocks(self):
        self.write("src/a.ts", "// @ts-ignore\n")
        result = self.scan(rules={"whitelist": {"ANGULAR.TS_IGNORE"}, "blacklist": set()})
        self.assertEqual(result["findings"], [])
        result = self.scan(rules={"whitelist": set(), "blacklist": {"ANGULAR.TS_IGNORE"}})
        self.assertEqual(result["findings"][0]["status"], "blocked")

    def test_ignores_non_matching_files(self):
        self.write("README.md", "this mentions NOSONAR in prose\n")
        self.assertEqual(self.scan()["findings"], [])


if __name__ == "__main__":
    unittest.main()
