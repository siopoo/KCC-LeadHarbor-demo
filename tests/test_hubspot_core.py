from __future__ import annotations

import logging
import unittest

import requests

from leadharbor.hubspot.batch import batch_create, batch_read
from leadharbor.hubspot.client import HubSpotClient, HubSpotError
from leadharbor.hubspot.companies import HubSpotCompanies
from leadharbor.hubspot.dedup import match_company, match_contact
from leadharbor.hubspot.diff import compare_properties
from leadharbor.hubspot.normalization import (
    normalize_company_name,
    normalize_domain,
    normalize_email,
    normalize_phone,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = "response body"

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def request(self, method: str, url: str, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


class FailingSession:
    def __init__(self):
        self.calls = 0

    def request(self, method: str, url: str, **kwargs):
        self.calls += 1
        raise requests.Timeout("network timeout")


class HubSpotNormalizationTests(unittest.TestCase):
    def test_domain_normalization_accepts_urls_but_rejects_arbitrary_text(self) -> None:
        samples = {
            "https://www.Example.com/about?q=1#top": "example.com",
            "http://example.com/": "example.com",
            "www.example.com/": "example.com",
            "EXAMPLE.COM": "example.com",
            "not a website": "",
            "https://localhost/test": "",
            "": "",
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_domain(raw), expected)

    def test_email_phone_and_company_name_normalization_is_deterministic(self) -> None:
        self.assertEqual(normalize_email("  Sales@Example.COM "), "sales@example.com")
        self.assertEqual(normalize_email("not-an-email"), "")
        self.assertEqual(normalize_phone("+1 (512) 555-0100 ext. 9"), "15125550100x9")
        self.assertEqual(normalize_phone("123"), "")
        self.assertEqual(
            normalize_company_name(" ABC Construction, L.L.C. "),
            "abc construction",
        )


class HubSpotDedupTests(unittest.TestCase):
    def test_company_exact_domain_is_an_exact_match(self) -> None:
        decision = match_company(
            {"name": "Acme Builders", "website": "https://acme.example", "market": "Texas"},
            [{"id": "42", "properties": {"name": "Acme", "domain": "www.acme.example"}}],
        )
        self.assertEqual(decision.kind, "EXACT_MATCH")
        self.assertEqual(decision.record_id, "42")
        self.assertEqual(decision.reason, "domain")

    def test_company_name_only_is_review_required_not_exact(self) -> None:
        decision = match_company(
            {"name": "ABC Construction LLC", "website": "", "market": "Texas"},
            [{"id": "7", "properties": {"name": "ABC Construction, L.L.C.", "state": "Florida"}}],
        )
        self.assertEqual(decision.kind, "POSSIBLE_MATCH")
        self.assertEqual(decision.record_id, "7")

    def test_contact_email_is_exact_but_phone_or_name_is_only_possible(self) -> None:
        exact = match_contact(
            {"email": "ANA@EXAMPLE.COM", "contact_first_name": "Ana", "contact_last_name": "Diaz"},
            [{"id": "11", "properties": {"email": "ana@example.com"}}],
        )
        self.assertEqual(exact.kind, "EXACT_MATCH")
        possible = match_contact(
            {"email": "", "phone": "512-555-0100", "contact_first_name": "Ana", "contact_last_name": "Diaz"},
            [{"id": "12", "properties": {"phone": "+1 512 555 0100", "firstname": "Ana", "lastname": "Diaz"}}],
        )
        self.assertEqual(possible.kind, "POSSIBLE_MATCH")

    def test_ambiguous_exact_identifiers_require_review(self) -> None:
        decision = match_contact(
            {"email": "ana@example.com"},
            [
                {"id": "11", "properties": {"email": "ana@example.com"}},
                {"id": "12", "properties": {"email": "ANA@example.com"}},
            ],
        )
        self.assertEqual(decision.kind, "AMBIGUOUS")
        self.assertEqual(decision.record_id, "")


class HubSpotDiffTests(unittest.TestCase):
    def test_diff_fills_only_empty_values_and_flags_conflicts(self) -> None:
        changes = compare_properties(
            {"phone": "", "industry": None, "city": "Dallas", "state": "TX"},
            {"phone": "+1 555", "industry": "Construction", "city": "Dallas", "state": "Texas", "website": ""},
        )
        by_field = {change.field: change for change in changes}
        self.assertEqual(by_field["phone"].action, "FILL_MISSING")
        self.assertEqual(by_field["industry"].action, "FILL_MISSING")
        self.assertEqual(by_field["city"].action, "NO_CHANGE")
        self.assertEqual(by_field["state"].action, "CONFLICT")
        self.assertNotIn("website", by_field)


class HubSpotClientTests(unittest.TestCase):
    def test_client_retries_429_using_retry_after_without_leaking_token(self) -> None:
        session = FakeSession([
            FakeResponse(429, {"message": "slow down"}, {"Retry-After": "2"}),
            FakeResponse(200, {"results": []}),
        ])
        sleeps: list[float] = []
        client = HubSpotClient("super-secret-token", session=session, sleep=sleeps.append)

        with self.assertLogs("leadharbor.hubspot", logging.WARNING) as captured:
            result = client.request("GET", "/crm/objects/2026-03/companies")

        self.assertEqual(result, {"results": []})
        self.assertEqual(sleeps, [2.0])
        self.assertEqual(len(session.requests), 2)
        self.assertNotIn("super-secret-token", "\n".join(captured.output))

    def test_client_does_not_retry_validation_error_and_returns_safe_category(self) -> None:
        session = FakeSession([FakeResponse(400, {"message": "Property invalid"})])
        client = HubSpotClient("secret", session=session, sleep=lambda _: None)

        with self.assertRaises(HubSpotError) as raised:
            client.request("POST", "/crm/objects/2026-03/companies", json={})

        self.assertEqual(raised.exception.category, "validation")
        self.assertEqual(len(session.requests), 1)
        self.assertNotIn("secret", str(raised.exception))

    def test_client_retries_temporary_server_failures_with_bounded_backoff(self) -> None:
        session = FakeSession([
            FakeResponse(503, {"message": "unavailable"}),
            FakeResponse(502, {"message": "bad gateway"}),
            FakeResponse(200, {"ok": True}),
        ])
        sleeps: list[float] = []
        client = HubSpotClient("secret", session=session, sleep=sleeps.append)

        with self.assertLogs("leadharbor.hubspot", logging.WARNING):
            result = client.request("GET", "/crm/objects/2026-03/companies")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(len(session.requests), 3)

    def test_search_calls_are_limited_to_four_per_second(self) -> None:
        session = FakeSession([FakeResponse(200), FakeResponse(200)])
        sleeps: list[float] = []
        client = HubSpotClient(
            "secret", session=session, sleep=sleeps.append, monotonic=lambda: 10.0,
        )

        client.request("POST", "/crm/objects/2026-03/companies/search", search=True, json={})
        client.request("POST", "/crm/objects/2026-03/contacts/search", search=True, json={})

        self.assertEqual(sleeps, [0.25])

    def test_batch_create_splits_more_than_one_hundred_inputs(self) -> None:
        first = [
            {"id": str(index), "objectWriteTraceId": str(index), "properties": {}}
            for index in range(100)
        ]
        second = [{"id": "100", "objectWriteTraceId": "100", "properties": {}}]
        session = FakeSession([
            FakeResponse(201, {"results": first}),
            FakeResponse(201, {"results": second}),
        ])
        client = HubSpotClient("secret", session=session, sleep=lambda _: None)

        outcomes = batch_create(
            client, "companies", [(str(index), {"name": f"Company {index}"}) for index in range(101)],
        )

        self.assertEqual(len(outcomes), 101)
        self.assertEqual([len(call["json"]["inputs"]) for call in session.requests], [100, 1])

    def test_batch_read_splits_ids_and_returns_records_by_id(self) -> None:
        first = [{"id": str(index), "properties": {"name": f"Company {index}"}} for index in range(100)]
        second = [{"id": "100", "properties": {"name": "Company 100"}}]
        session = FakeSession([
            FakeResponse(200, {"results": first}),
            FakeResponse(200, {"results": second}),
        ])
        client = HubSpotClient("secret", session=session, sleep=lambda _: None)

        records = batch_read(
            client, "companies", [str(index) for index in range(101)], ["name", "domain"],
        )

        self.assertEqual(records["100"]["properties"]["name"], "Company 100")
        self.assertEqual([len(call["json"]["inputs"]) for call in session.requests], [100, 1])

    def test_network_timeout_is_bounded_and_reported_safely(self) -> None:
        session = FailingSession()
        client = HubSpotClient(
            "secret-token", session=session, sleep=lambda _: None, max_attempts=2,
        )

        with self.assertLogs("leadharbor.hubspot", logging.WARNING):
            with self.assertRaises(HubSpotError) as raised:
                client.request("GET", "/crm/objects/2026-03/companies")

        self.assertEqual(raised.exception.category, "network")
        self.assertEqual(session.calls, 2)
        self.assertNotIn("secret-token", str(raised.exception))

    def test_authentication_and_permission_errors_are_not_retried(self) -> None:
        for status, category in ((401, "invalid_credentials"), (403, "missing_permissions")):
            with self.subTest(status=status):
                session = FakeSession([FakeResponse(status, {"message": "denied"})])
                client = HubSpotClient("secret", session=session, sleep=lambda _: None)
                with self.assertRaises(HubSpotError) as raised:
                    client.request("GET", "/crm/objects/2026-03/companies")
                self.assertEqual(raised.exception.category, category)
                self.assertEqual(len(session.requests), 1)

    def test_fresh_read_bypasses_preloaded_record_for_prewrite_check(self) -> None:
        session = FakeSession([
            FakeResponse(200, {"results": [{"name": "name"}, {"name": "domain"}]}),
            FakeResponse(200, {"results": [{"id": "42", "updatedAt": "old", "properties": {"name": "Acme"}}]}),
            FakeResponse(200, {"id": "42", "updatedAt": "new", "properties": {"name": "Acme"}}),
        ])
        companies = HubSpotCompanies(HubSpotClient("secret", session=session, sleep=lambda _: None))
        companies.preload(["42"])

        current = companies.get_fresh("42")

        self.assertEqual(current["updatedAt"], "new")
        self.assertEqual(len(session.requests), 3)


if __name__ == "__main__":
    unittest.main()
