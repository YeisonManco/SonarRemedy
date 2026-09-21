"""Tests for the suppression detector (line-level findings + certainty tiers)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class TypeSafeLegitimacyScoringTests(unittest.TestCase):
    """Advisory-only, opt-in-via-env-var: zero behavior change with no key.

    sonar_suppressions.py findings have no "rule"/"category"/"status" fields
    (unlike sonar_exclusions_report.py); they have "kind"/"certainty"/"match"
    instead. Only "ambiguous" findings are scored -- "certain" ones are
    already an unambiguous, settled detection with nothing for TypeSafe to
    adjudicate, mirroring how exclusions_report skips already-"blocked" ones.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("TYPESAFE_API_KEY", None)

    def write(self, rel: str, content: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_no_key_leaves_findings_unchanged_and_makes_no_calls(self):
        self.write("src/a.py", "x = 1  # NOSONAR\n")
        with mock.patch("typesafe_client.legitimacy_score") as scorer:
            result = sonar_suppressions.scan(self.root)
        scorer.assert_not_called()
        for finding in result["findings"]:
            self.assertNotIn("typesafe_legitimacy", finding)

    def test_key_set_scores_ambiguous_findings_only(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        self.write("src/a.py", "x = 1  # NOSONAR\n")  # ambiguous (nosonar_bare)
        self.write("src/b.cs", "#pragma warning disable CS0168\n")  # certain
        with mock.patch(
            "typesafe_client.legitimacy_score", return_value={"status": "ok", "noul": 0.2}
        ) as scorer:
            result = sonar_suppressions.scan(self.root)
        self.assertEqual(scorer.call_count, 1)
        scored = [f for f in result["findings"] if "typesafe_legitimacy" in f]
        self.assertEqual(len(scored), 1)
        self.assertEqual(scored[0]["certainty"], "ambiguous")
        certain = next(f for f in result["findings"] if f["certainty"] == "certain")
        self.assertNotIn("typesafe_legitimacy", certain)

    def test_scoring_is_bounded_to_max_scored(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        content = "".join(f"x{i} = 1  # noqa: F401\n" for i in range(5))
        self.write("src/a.py", content)
        with mock.patch(
            "typesafe_client.legitimacy_score", return_value={"status": "ok", "noul": 0.2}
        ) as scorer:
            with mock.patch.object(sonar_suppressions, "MAX_SCORED", 2):
                sonar_suppressions.scan(self.root)
        self.assertEqual(scorer.call_count, 2)


class TruncationDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel: str, content: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_truncated_false_when_cap_not_hit(self):
        self.write("src/a.py", "x = 1  # NOSONAR\n")
        result = sonar_suppressions.scan(self.root)
        self.assertFalse(result["truncated"])
        self.assertNotIn("stopped early", result["notice"])

    def test_truncated_true_when_cap_hit(self):
        content = "".join(f"x{i} = 1  # NOSONAR\n" for i in range(5))
        self.write("src/a.py", content)
        with mock.patch.object(sonar_suppressions, "MAX_FINDINGS", 2):
            result = sonar_suppressions.scan(self.root)
        self.assertTrue(result["truncated"])
        self.assertIn("stopped early", result["notice"])
        self.assertIn("2-finding cap", result["notice"])


if __name__ == "__main__":
    unittest.main()
