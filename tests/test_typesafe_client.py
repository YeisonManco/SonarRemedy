"""typesafe_client never raises; missing key or any transport failure degrades
to a data-only result. No real network call is made in these tests: the
module's opener factory is monkeypatched, mirroring how test_sonar_client.py
fakes its HTTP boundary (a substitutable callable) rather than opening real
sockets."""

import json
import os
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

import typesafe_client as tc


class FakeResponse:
    def __init__(self, body: bytes, url: str = tc.ENDPOINT):
        self.body = body
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self, limit):
        return self.body[:limit]

    def geturl(self):
        return self.url


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def ok_body(noul=0.87):
    return json.dumps({"answers": {"addresses_issue": {"type": "noul", "noul": noul}}}).encode()


class PrecheckProposalTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("TYPESAFE_API_KEY", None)

    def test_missing_key_returns_unavailable_without_network_call(self):
        fake = FakeOpener()
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal("csharpsquid:S2094", "smells", "src/A.cs", [])
        self.assertEqual(result, {"status": "unavailable", "reason": "TYPESAFE_API_KEY not set"})
        self.assertEqual(fake.calls, [])

    def test_successful_response_returns_ok_and_noul(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        fake = FakeOpener(response=FakeResponse(ok_body(0.87)))
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal(
                "csharpsquid:S2094", "smells", "src/A.cs", [{"old": "a", "new": "b"}]
            )
        self.assertEqual(result, {"status": "ok", "noul": 0.87})
        self.assertEqual(len(fake.calls), 1)
        request, timeout = fake.calls[0]
        self.assertEqual(timeout, 15)
        self.assertEqual(request.full_url, tc.ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        sent = json.loads(request.data)
        self.assertEqual(sent["state"]["rule"], "csharpsquid:S2094")
        self.assertEqual(sent["state"]["kind"], "smells")
        self.assertEqual(sent["state"]["path"], "src/A.cs")
        self.assertEqual(sent["state"]["edits"], [{"old": "a", "new": "b"}])
        self.assertEqual(sent["model"], "jev-latest")
        self.assertEqual(sent["questions"]["addresses_issue"]["type"], "noul")

    def test_custom_timeout_is_forwarded(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        fake = FakeOpener(response=FakeResponse(ok_body()))
        with mock.patch("typesafe_client._opener", return_value=fake):
            tc.precheck_proposal("rule", "kind", "path", [], timeout=5)
        self.assertEqual(fake.calls[0][1], 5)

    def test_network_error_returns_error_without_raising(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        fake = FakeOpener(error=URLError("no route to host"))
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal("rule", "kind", "path", [])
        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret-token", result["reason"])

    def test_timeout_returns_error_without_raising(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        fake = FakeOpener(error=TimeoutError("timed out"))
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal("rule", "kind", "path", [])
        self.assertEqual(result["status"], "error")

    def test_http_error_status_returns_error_without_raising(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        error = HTTPError(tc.ENDPOINT, 429, "Too Many Requests", hdrs=None, fp=None)
        fake = FakeOpener(error=error)
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal("rule", "kind", "path", [])
        self.assertEqual(result["status"], "error")

    def test_malformed_json_returns_error_without_raising(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        fake = FakeOpener(response=FakeResponse(b"not json"))
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal("rule", "kind", "path", [])
        self.assertEqual(result["status"], "error")
        self.assertNotIn("not json", result["reason"])

    def test_unexpected_shape_returns_error_without_raising(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        body = json.dumps({"unexpected": True}).encode()
        fake = FakeOpener(response=FakeResponse(body))
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal("rule", "kind", "path", [])
        self.assertEqual(result["status"], "error")

    def test_out_of_range_noul_returns_error_without_raising(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        fake = FakeOpener(response=FakeResponse(ok_body(1.5)))
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal("rule", "kind", "path", [])
        self.assertEqual(result["status"], "error")

    def test_redirected_response_is_rejected(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        fake = FakeOpener(response=FakeResponse(ok_body(), url="https://evil.example/steal"))
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal("rule", "kind", "path", [])
        self.assertEqual(result["status"], "error")

    def test_oversized_response_is_rejected(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        huge = b" " * (tc.RESPONSE_LIMIT + 1) + ok_body()
        fake = FakeOpener(response=FakeResponse(huge))
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal("rule", "kind", "path", [])
        self.assertEqual(result["status"], "error")

    def test_edits_are_bounded_to_a_small_budget(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        fake = FakeOpener(response=FakeResponse(ok_body()))
        huge_edit = {"old": "x" * 3000, "new": "y" * 3000}
        with mock.patch("typesafe_client._opener", return_value=fake):
            tc.precheck_proposal("rule", "kind", "path", [huge_edit, dict(huge_edit)])
        sent = json.loads(fake.calls[0][0].data)
        total_len = sum(
            len(edit.get("old", "")) + len(edit.get("new", "")) for edit in sent["state"]["edits"]
        )
        self.assertLessEqual(total_len, tc.MAX_EDIT_CHARS)

    def test_unreadable_edit_entries_do_not_raise(self):
        os.environ["TYPESAFE_API_KEY"] = "secret-token"
        fake = FakeOpener(response=FakeResponse(ok_body()))
        with mock.patch("typesafe_client._opener", return_value=fake):
            result = tc.precheck_proposal("rule", "kind", "path", [{}, {"old": None, "new": 5}])
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
