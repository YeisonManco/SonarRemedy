# Python Skills Index (python)

Compact map for Python technical-debt workers: which Python tool answers which debt problem, and where the authoritative docs live. **Load on demand; never copy tool source.**

## Debt problem → tool → authoritative docs

| Debt problem | Tool | What it fixes | Docs |
|---|---|---|---|
| Code smells (unused imports, complexity, style) | `ruff` | Linter + formatter; replaces flake8/pylint/isort/black | https://docs.astral.sh/ruff/ |
| Type errors / latent bugs | `mypy` | Static type checker | https://mypy.readthedocs.io/ |
| Security vulnerabilities | `bandit` | SAST for Python | https://bandit.readthedocs.io/ |
| Dependency vulnerabilities | `pip-audit` | Audit dependencies against PyPI/OSV | https://pypi.org/project/pip-audit/ |
| Test coverage | `coverage.py` (+ `pytest-cov`) | Measure and report coverage | https://coverage.readthedocs.io/ |
| Duplication | `pylint` (`duplicate-code`) | Detect duplicated code blocks | https://pylint.readthedocs.io/ |
| Test quality | `pytest` (+ `hypothesis`) | Tests and property-based testing | https://docs.pytest.org/ |
| Formatting | `ruff format` | Deterministic formatting | https://docs.astral.sh/ruff/formatter/ |

## How to use

For a debt job, pick the tool by the debt problem, read only its relevant docs, and respect the target repo's own config (`pyproject.toml`, `setup.cfg`, `tox.ini`, `.ruff.toml`) and pinned versions over global defaults. Workers are **proposal-only**: this index is reference material — never install tools, change dependencies, or run scanners. Only the serial integrator runs the target's configured checks.

The same pattern extends to other ecosystems (JavaScript/TypeScript, Java, Go): add a `<lang>-index.md` mapping that language's debt problems to its authoritative tools.
