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


def precheck_proposal(
    rule: str, kind: str, path: str, edits: list[dict[str, Any]], *, timeout: int = 15
) -> dict[str, Any]:
    """Ask a cheap yes/no question: does this diff plausibly address the rule?

    Advisory-only. Reads TYPESAFE_API_KEY from the environment only (never
    config/prompt/args). Missing key returns immediately with no network
    call. Any failure at all -- network, timeout, non-2xx, malformed or
    unexpected response -- is caught and returned as data; this function
    never raises.
    """
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return {"status": "unavailable", "reason": "TYPESAFE_API_KEY not set"}
    try:
        payload = {
            "state": {
                "rule": str(rule),
                "kind": str(kind),
                "path": str(path),
                "edits": _bound_edits(edits if isinstance(edits, list) else []),
            },
            "model": MODEL,
            "questions": {
                "addresses_issue": {
                    "type": "noul",
                    "instructions": (
                        "Does this code change plausibly address the described Sonar rule "
                        "violation, given the before/after text?"
                    ),
                    "criteria": {
                        "true": "The edit is relevant to the rule and changes the flagged pattern",
                        "false": (
                            "The edit is unrelated to the rule or doesn't touch the flagged pattern"
                        ),
                    },
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
        noul = body["answers"]["addresses_issue"]["noul"]
        if isinstance(noul, bool) or not isinstance(noul, (int, float)) or not 0 <= noul <= 1:
            return {"status": "error", "reason": "unexpected noul value"}
        return {"status": "ok", "noul": float(noul)}
    except Exception as error:  # advisory-only: this call must never raise
        return {"status": "error", "reason": _safe_reason(error)}
