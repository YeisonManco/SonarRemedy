"""Advisory-only TypeSafe (Jev/System One) pre-check. Stdlib-only; never raises.

Mirrors sonar_client.py's HTTP hygiene: no ambient proxy, TLS via the
platform's default trust store, no redirects followed, a bounded response
size and a bounded timeout. Unlike sonar_client.Client, this endpoint is a
single hardcoded constant (not user-configured), so the request/response
shape here is deliberately minimal.
"""

import json
import os
import ssl
from typing import Any
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from sonar_client import NoRedirect

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
# Cheap advisory call: bound the combined old+new text sent, not the full diff.
MAX_EDIT_CHARS = 4000
RESPONSE_LIMIT = 64 * 1024


def _opener():
    # Ignore ambient proxy settings and never follow redirects, matching
    # sonar_client.Client's trust boundary.
    return build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=ssl.create_default_context()),
        NoRedirect(),
    )


_TRUNCATION_MARKER = "...[truncated]"


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(_TRUNCATION_MARKER):
        return _TRUNCATION_MARKER[:limit]
    return text[: limit - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _bound_edits(edits: list[dict[str, Any]], budget: int = MAX_EDIT_CHARS) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    remaining = budget
    for edit in edits:
        if remaining <= 0:
            break
        old = str(edit.get("old", "")) if isinstance(edit, dict) else ""
        new = str(edit.get("new", "")) if isinstance(edit, dict) else ""
        total = len(old) + len(new)
        if total > remaining:
            old_budget = remaining // 2
            old, new = _cut(old, old_budget), _cut(new, remaining - old_budget)
            remaining = 0
        else:
            remaining -= total
        bounded.append({"old": old, "new": new})
    return bounded


def _safe_reason(error: Exception) -> str:
    """A short, disk-safe description: no secrets, no raw response bodies."""
    from urllib.error import HTTPError, URLError

    if isinstance(error, HTTPError):
        return f"http error {error.code}"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, URLError):
        return "network error"
    if isinstance(error, (json.JSONDecodeError, KeyError, TypeError, ValueError, LookupError)):
        return "malformed or unexpected response"
    return "precheck failed: " + type(error).__name__


def _ask_noul(
    state: Any,
    instructions: str,
    criteria: dict[str, str],
    *,
    timeout: int = 15,
    question_key: str = "result",
) -> dict[str, Any]:
    """Ask System One a single cheap yes/no ("noul") question about `state`.

    Shared HTTP/security boilerplate for every advisory TypeSafe question this
    pack asks: builds the request, calls `_opener()`, enforces a bounded
    response, and is exception-safe. Reads TYPESAFE_API_KEY from the
    environment only (never config/prompt/args). Missing key returns
    immediately with no network call. Any failure at all -- network, timeout,
    non-2xx, malformed or unexpected response -- is caught and returned as
    data; this function never raises.
    """
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return {"status": "unavailable", "reason": "TYPESAFE_API_KEY not set"}
    try:
        payload = {
            "state": state,
            "model": MODEL,
            "questions": {
                question_key: {
                    "type": "noul",
                    "instructions": instructions,
                    "criteria": criteria,
                }
            },
        }
        data = json.dumps(payload).encode("utf-8")
        request = Request(
            ENDPOINT,
            data=data,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        with _opener().open(request, timeout=timeout) as response:
            if response.geturl() != request.full_url:
                return {"status": "error", "reason": "response endpoint changed"}
            raw = response.read(RESPONSE_LIMIT + 1)
        if len(raw) > RESPONSE_LIMIT:
            return {"status": "error", "reason": "response byte budget exceeded"}
        body = json.loads(raw)
        noul = body["answers"][question_key]["noul"]
        if isinstance(noul, bool) or not isinstance(noul, (int, float)) or not 0 <= noul <= 1:
            return {"status": "error", "reason": "unexpected noul value"}
        return {"status": "ok", "noul": float(noul)}
    except Exception as error:  # advisory-only: this call must never raise
        return {"status": "error", "reason": _safe_reason(error)}


def precheck_proposal(
    rule: str, kind: str, path: str, edits: list[dict[str, Any]], *, timeout: int = 15
) -> dict[str, Any]:
    """Ask a cheap yes/no question: does this diff plausibly address the rule?

    Advisory-only. Same never-raises, opt-in-via-env-var contract as
    `_ask_noul`; see that docstring for the shared behavior.
    """
    state = {
        "rule": str(rule),
        "kind": str(kind),
        "path": str(path),
        "edits": _bound_edits(edits if isinstance(edits, list) else []),
    }
    instructions = (
        "Does this code change plausibly address the described Sonar rule "
        "violation, given the before/after text?"
    )
    criteria = {
        "true": "The edit is relevant to the rule and changes the flagged pattern",
        "false": "The edit is unrelated to the rule or doesn't touch the flagged pattern",
    }
    return _ask_noul(state, instructions, criteria, timeout=timeout, question_key="addresses_issue")


def hotspot_risk_score(rule: str, path: str, source: str, *, timeout: int = 15) -> dict[str, Any]:
    """Ask a cheap yes/no question: does this security hotspot look like a genuine risk?

    Advisory-only triage signal for a human reviewing a deferred security
    hotspot -- `debt_queue.plan()` always defers `hotspots`-kind issues for
    human review and this never changes that; it only attaches an extra
    signal the human sees alongside the deferred job. Same never-raises,
    opt-in-via-env-var contract as `_ask_noul`; see that docstring for the
    shared behavior.
    """
    state = {
        "rule": str(rule),
        "path": str(path),
        # Bounded the same way precheck_proposal bounds edit text: never the
        # full file/source excerpt available at intake.
        "source": _cut(str(source), MAX_EDIT_CHARS),
    }
    instructions = (
        "Does this security hotspot look like a genuine risk requiring careful review, "
        "or does it look like a likely false positive / already-safe pattern?"
    )
    criteria = {
        "true": "The pattern looks like a real, exploitable risk in this context",
        "false": "The pattern looks safe, already mitigated, or a likely false positive",
    }
    return _ask_noul(state, instructions, criteria, timeout=timeout, question_key="genuine_risk")


def legitimacy_score(
    rule: str, category: str, file: str, evidence: str, *, timeout: int = 15
) -> dict[str, Any]:
    """Ask a cheap yes/no question: does this exclusion/suppression look legitimate?

    Advisory-only triage signal for a human reviewing NOSONAR/@ts-ignore/etc.
    findings -- never auto-blocks or auto-allows anything. Same never-raises,
    opt-in-via-env-var contract as `_ask_noul`; see that docstring for the
    shared behavior.
    """
    state = {
        "rule": str(rule),
        "category": str(category),
        "file": str(file),
        "evidence": str(evidence),
    }
    instructions = (
        "Does this Sonar-evasion directive (NOSONAR, @ts-ignore, #pragma, NoWarn, "
        "coverage exclusion, etc.) look like a legitimate, justified exception rather "
        "than someone silencing a real issue?"
    )
    criteria = {
        "true": (
            "The directive includes a reason, links to a ticket, or the surrounding "
            "context makes the exception clearly justified"
        ),
        "false": "No justification is visible, or it looks like the finding is just being silenced",
    }
    return _ask_noul(state, instructions, criteria, timeout=timeout, question_key="legitimate")
