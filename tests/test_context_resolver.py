"""Tests for the source context resolver (enclosing brace block)."""

import unittest

import context_resolver as cr


class BraceDepthTests(unittest.TestCase):
    def test_simple_method(self):
        lines = [
            "class A\n",
            "{\n",
            "    int Value()\n",
            "    {\n",
            "        return 1;\n",  # issue here (index 4), depth 2
            "    }\n",
            "}\n",
        ]
        depths = cr.brace_depths(lines)
        self.assertEqual(depths[0], 0)  # before class A
        self.assertEqual(depths[1], 0)  # before class {
        self.assertEqual(depths[2], 1)  # inside class body
        self.assertEqual(depths[3], 1)  # before method {
        self.assertEqual(depths[4], 2)  # inside method
        self.assertEqual(depths[5], 2)  # before method }
        self.assertEqual(depths[6], 1)  # before class }

    def test_brace_in_string_not_counted(self):
        lines = ['var s = "{" + "}";\n', "class A { }\n"]
        depths = cr.brace_depths(lines)
        self.assertEqual(depths[1], 0)  # the string braces do not change depth

    def test_line_comment_brace_not_counted(self):
        lines = ["// { } not real\n", "class A { }\n"]
        depths = cr.brace_depths(lines)
        self.assertEqual(depths[1], 0)


class EnclosingBlockTests(unittest.TestCase):
    def test_returns_enclosing_method(self):
        lines = [
            "class A\n",
            "{\n",
            "    int Value()\n",
            "    {\n",
            "        return 1;\n",  # index 4
            "    }\n",
            "}\n",
        ]
        block = cr.enclosing_block(lines, 4)
        self.assertEqual(block["start_line"], 4)  # method {
        self.assertEqual(block["end_line"], 6)  # method }
        self.assertFalse(block["partial"])

    def test_file_level_falls_back_to_window(self):
        lines = ["namespace X;\n", "using System;\n", "var x = 1;\n"]
        block = cr.enclosing_block(lines, 2)
        self.assertEqual(block["start_line"], 1)
        self.assertEqual(block["end_line"], 3)

    def test_oversized_block_is_partial_and_centered(self):
        lines = ["{\n"] + ["    x += 1;\n"] * 500 + ["}\n"]
        block = cr.enclosing_block(lines, 250, max_lines=100)
        self.assertTrue(block["partial"])
        self.assertLessEqual(block["end_line"] - block["start_line"] + 1, 100)
        # the issue line 250 must be inside the returned window
        self.assertLessEqual(block["start_line"], 251)
        self.assertGreaterEqual(block["end_line"], 251)

    def test_rejects_bad_bounds(self):
        with self.assertRaises(ValueError):
            cr.enclosing_block([], 0, max_lines=3)


class LanguageAwarePartialTests(unittest.TestCase):
    """A brace depth of 0 means something different in Python/VB than in C#:
    those languages never use `{`/`}` for blocks at all, so depth-0 is not
    evidence of a real file-level scope -- the old code returned a
    confidently-wrong `partial=False` there regardless of language."""

    def test_python_like_file_marks_partial_true_not_false(self):
        lines = [
            "def outer():\n",
            "    x = 1\n",
            "    return x\n",  # issue here (index 2), indentation-based block
        ]
        block = cr.enclosing_block(lines, 2, path="module.py")
        self.assertTrue(block["partial"])

    def test_vb_file_marks_partial_true_not_false(self):
        lines = [
            "Module M\n",
            "    Sub Main()\n",
            "        Console.WriteLine(1)\n",  # issue here (index 2)
            "    End Sub\n",
            "End Module\n",
        ]
        block = cr.enclosing_block(lines, 2, path="module.vb")
        self.assertTrue(block["partial"])

    def test_known_brace_language_top_level_stays_confident(self):
        lines = ["using System;\n", "var x = 1;\n"]
        block = cr.enclosing_block(lines, 1, path="Program.cs")
        self.assertFalse(block["partial"])

    def test_missing_path_defaults_to_conservative(self):
        # The real caller (debt_queue.py) does not currently pass a path, so
        # the safe default -- when the language is unknown -- must also be
        # conservative rather than silently confident.
        lines = ["namespace X;\n", "using System;\n", "var x = 1;\n"]
        block = cr.enclosing_block(lines, 2)
        self.assertTrue(block["partial"])


if __name__ == "__main__":
    unittest.main()
