"""Resolve the minimal-but-sufficient source context for a finding.

A fixed ±8-line window is too blind for most fixes: the worker needs the whole
enclosing scope (method/class body) to see the types, parameters and surrounding
logic. This module finds the enclosing brace block for a line and returns it
bounded to a budget — the worker sees the relevant scope instead of a raw slice,
without ever dumping the whole file.

Stdlib only; the brace scan is a rough lexer (strings/comments tracked so a `{`
inside a string literal does not shift the block). A slightly-off window is
advisory: the worker can still request the full file via `need_more_context`.
"""

import os
from typing import Any

DEFAULT_MAX_LINES = 120

# Extensions whose grammar uses brace-delimited blocks (`{`/`}`), matching
# sonar_remedy.EXTENSION_LANGUAGE's brace-based languages. Kept as a small
# local allowlist rather than importing sonar_remedy: this module is a lean,
# stdlib-only leaf utility and sonar_remedy is the large top-level facade, so
# importing it here would invert the dependency direction for no real gain.
BRACE_DELIMITED_EXTENSIONS = frozenset(
    {
        ".cs",
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
    }
)


def is_brace_delimited(path: str | None) -> bool:
    """True only when `path`'s extension is a KNOWN brace-delimited language.

    An unknown, missing, or explicitly non-brace extension (e.g. `.py`,
    `.vb`) returns False: a brace depth of 0 there is not real evidence that
    a window is the true enclosing scope, so the caller should stay
    conservative instead of confidently claiming a complete window.
    """
    if not path:
        return False
    return os.path.splitext(path)[1].lower() in BRACE_DELIMITED_EXTENSIONS


def brace_depths(lines: list[str]) -> list[int]:
    """Brace depth BEFORE each line, ignoring braces in strings/comments."""
    depths: list[int] = []
    depth = 0
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    for line in lines:
        depths.append(depth)
        i = 0
        while i < len(line):
            ch = line[i]
            nxt = line[i + 1] if i + 1 < len(line) else ""
            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                if ch == "*" and nxt == "/":
                    in_block_comment = False
                    i += 2
                else:
                    i += 1
                continue
            if in_string:
                if ch == "\\":
                    i += 2
                elif ch == '"':
                    in_string = False
                    i += 1
                else:
                    i += 1
                continue
            if in_char:
                if ch == "\\":
                    i += 2
                elif ch == "'":
                    in_char = False
                    i += 1
                else:
                    i += 1
                continue
            if ch == "/" and nxt == "/":
                in_line_comment = True
                i += 2
                continue
            if ch == "/" and nxt == "*":
                in_block_comment = True
                i += 2
                continue
            if ch == '"':
                in_string = True
                i += 1
                continue
            if ch == "'":
                in_char = True
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
    return depths


def enclosing_block(
    lines: list[str],
    line_index: int,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    path: str | None = None,
) -> dict[str, Any]:
    """Return the enclosing brace block for `line_index` (0-based), bounded.

    Returns `{start_line, end_line, partial}` with 1-based inclusive lines. When
    the block exceeds `max_lines` it is centered on the finding and `partial` is
    True (the worker may then request the full file).

    `path` (optional) lets the caller identify the source language by
    extension. For a language that does not use brace-delimited blocks at
    all (Python, VB.NET, ...), or when `path` is not given, a brace depth of
    0 carries no real information about the true enclosing scope, so the
    result is marked `partial=True` instead of silently claiming a complete,
    accurate window.
    """
    n = len(lines)
    if type(max_lines) is not int or not 8 <= max_lines <= 500:
        raise ValueError("max_lines must be 8..500")
    depths = brace_depths(lines)
    if not 0 <= line_index < n:
        return {"start_line": 1, "end_line": n, "partial": False}
    target = depths[line_index]
    if target <= 0:
        # Not inside any brace block (file/namespace level) -- OR the
        # language isn't brace-delimited at all, so depth 0 is meaningless.
        start = max(0, line_index - max_lines // 2)
        end = min(n, start + max_lines)
        truncated = end - start >= max_lines
        partial = truncated or not is_brace_delimited(path)
        return {"start_line": start + 1, "end_line": end, "partial": partial}
    # The block opens on the last line at/above `line_index` whose depth is below
    # `target` (that line holds the `{`), and closes on the first line at/after
    # `line_index` whose NEXT line drops below `target` (that line holds the `}`).
    opening = line_index
    while opening > 0 and depths[opening] >= target:
        opening -= 1
    closing = line_index
    while closing < n - 1 and depths[closing + 1] >= target:
        closing += 1
    block_len = closing - opening + 1
    partial = block_len > max_lines
    if partial:
        center = line_index
        opening = max(opening, center - max_lines // 2)
        closing = min(closing, opening + max_lines - 1)
        if closing < opening:
            closing = min(n - 1, opening + max_lines - 1)
    return {"start_line": opening + 1, "end_line": closing + 1, "partial": partial}
