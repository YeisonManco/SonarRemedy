"""Tests for the suppression detector (line-level findings + certainty tiers)."""

import tempfile
import unittest
from pathlib import Path

import sonar_suppressions


class SuppressionScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel: str, content: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def scan(self) -> dict:
        return sonar_suppressions.scan(self.root)

    def test_detects_certain_pragma(self):
        self.write("src/b.cs", "#pragma warning disable CS0168\n")
        result = self.scan()
        kinds = {f["kind"] for f in result["findings"]}
        self.assertIn("pragma_warning_disable", kinds)
        finding = next(f for f in result["findings"] if f["kind"] == "pragma_warning_disable")
        self.assertEqual(finding["certainty"], "certain")

    def test_rule_specific_nosonar_is_certain(self):
        self.write("src/a.py", "x = 1  # NOSONAR:python:S1234\n")
        finding = self.scan()["findings"][0]
        self.assertEqual(finding["kind"], "nosonar_rule_specific")
        self.assertEqual(finding["certainty"], "certain")

    def test_bare_nosonar_is_ambiguous(self):
        self.write("src/a.py", "x = 1  # NOSONAR\n")
        finding = self.scan()["findings"][0]
        self.assertEqual(finding["kind"], "nosonar_bare")
        self.assertEqual(finding["certainty"], "ambiguous")

    def test_noqa_is_ambiguous(self):
        self.write("src/a.py", "import os  # noqa: F401\n")
        finding = self.scan()["findings"][0]
        self.assertEqual(finding["kind"], "noqa")
        self.assertEqual(finding["certainty"], "ambiguous")

    def test_python_and_java_are_scanned(self):
        self.write("src/a.py", "x = 1  # NOSONAR\n")
        self.write("src/B.java", '@SuppressWarnings("unchecked") class B {}\n')
        result = self.scan()
        kinds = {f["kind"] for f in result["findings"]}
        self.assertIn("nosonar_bare", kinds)
        self.assertIn("suppress_warnings", kinds)

    def test_reports_file_and_line(self):
        self.write("src/a.py", "x = 1\n# NOSONAR\n")
        finding = self.scan()["findings"][0]
        self.assertEqual(finding["line"], 2)
        self.assertTrue(finding["file"].endswith("a.py"))

    def test_counts_certain_and_ambiguous(self):
        self.write("src/a.py", "#pragma warning disable CS0168\nimport os  # noqa: F401\n")
        result = self.scan()
        self.assertEqual(result["counts"]["certain"], 1)
        self.assertEqual(result["counts"]["ambiguous"], 1)

    def test_ignores_non_code_files(self):
        self.write("README.md", "this mentions NOSONAR in prose\n")
        result = self.scan()
        self.assertEqual(result["findings"], [])


if __name__ == "__main__":
    unittest.main()
