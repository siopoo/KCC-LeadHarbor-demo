from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from leadharbor.hubspot.client import HubSpotError
from leadharbor.models import Lead
from web_app import create_app, hubspot_access_token


class FakeHubSpotClient:
    def __init__(self, error: HubSpotError | None = None):
        self.error = error
        self.calls = 0

    def test_connection(self) -> dict:
        self.calls += 1
        if self.error:
            raise self.error
        return {"results": []}


class FakeHubSpotService:
    def __init__(self):
        self.check_calls: list[list[int]] = []
        self.sync_calls: list[tuple[int, list[dict]]] = []

    def check(self, company_ids: list[int]) -> dict:
        self.check_calls.append(company_ids)
        return {
            "batch_id": 4,
            "summary": {"new": 1, "duplicate": 0, "enrichable": 0, "conflict": 0, "failed": 0},
            "results": [{
                "company_id": company_ids[0], "company_name": "Acme", "status": "NEW",
                "match_reason": "", "match_confidence": "none",
                "hubspot_company_id": "", "hubspot_contact_id": "",
                "company_properties": {"name": "Acme"}, "contact_properties": {},
                "company_differences": [], "contact_differences": [], "error": "",
            }],
        }

    def sync(self, batch_id: int, approvals: list[dict]) -> dict:
        self.sync_calls.append((batch_id, approvals))
        return {
            "summary": {"success": 1, "failed": 0, "skipped": 0, "conflict": 0},
            "results": [{"company_id": approvals[0]["company_id"], "status": "SYNCED", "error": ""}],
        }


class HubSpotWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp_dir.name) / "web.db")
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.db = self.app.config["DATABASE"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _csrf(self) -> str:
        self.client.get("/settings")
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def test_settings_saves_masks_and_removes_hubspot_token(self) -> None:
        secret = "pat-na1-private-secret-abcd"
        saved = self.client.post("/settings", data={
            "csrf_token": self._csrf(), "settings_section": "hubspot",
            "hubspot_access_token": secret,
        }, follow_redirects=True)

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.db.get_setting("hubspot_access_token"), secret)
        self.assertIn("••••••••••••abcd".encode(), saved.data)
        self.assertNotIn(secret.encode(), saved.data)

        removed = self.client.post("/settings", data={
            "csrf_token": self._csrf(), "settings_section": "hubspot",
            "remove_hubspot_token": "on",
        }, follow_redirects=True)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.db.get_setting("hubspot_access_token"), "")

    def test_environment_token_has_priority_and_is_never_returned(self) -> None:
        self.db.set_setting("hubspot_access_token", "stored-token-1111")
        with patch.dict(os.environ, {"HUBSPOT_ACCESS_TOKEN": "environment-token-9999"}):
            self.assertEqual(hubspot_access_token(self.db), "environment-token-9999")
            page = self.client.get("/settings")
        self.assertIn("••••••••••••9999".encode(), page.data)
        self.assertNotIn(b"environment-token-9999", page.data)
        self.assertNotIn(b"stored-token-1111", page.data)

    def test_connection_returns_friendly_status_without_token(self) -> None:
        self.db.set_setting("hubspot_access_token", "secret-token")
        fake = FakeHubSpotClient()
        with patch("web_app.build_hubspot_client", return_value=fake):
            response = self.client.post(
                "/api/integrations/hubspot/test",
                headers={"X-CSRF-Token": self._csrf()},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "connected")
        self.assertNotIn("secret-token", response.get_data(as_text=True))

        denied = FakeHubSpotClient(HubSpotError(
            "missing_permissions", "HubSpot permissions are missing.", 403,
        ))
        with patch("web_app.build_hubspot_client", return_value=denied):
            response = self.client.post(
                "/api/integrations/hubspot/test",
                headers={"X-CSRF-Token": self._csrf()},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["category"], "missing_permissions")

    def test_check_and_sync_endpoints_require_csrf_and_explicit_selection(self) -> None:
        company_id = self.db.create_company(Lead(name="Acme", market="Texas"))
        self.db.set_setting("hubspot_access_token", "secret-token")
        service = FakeHubSpotService()
        with patch("web_app.build_hubspot_service", return_value=service):
            invalid = self.client.post(
                "/api/integrations/hubspot/check", json={"company_ids": [company_id]},
            )
            self.assertEqual(invalid.status_code, 400)
            empty = self.client.post(
                "/api/integrations/hubspot/check", json={"company_ids": []},
                headers={"X-CSRF-Token": self._csrf()},
            )
            self.assertEqual(empty.status_code, 400)
            checked = self.client.post(
                "/api/integrations/hubspot/check", json={"company_ids": [company_id]},
                headers={"X-CSRF-Token": self._csrf()},
            )
            self.assertEqual(checked.status_code, 200)
            synced = self.client.post(
                "/api/integrations/hubspot/sync",
                json={"batch_id": 4, "approvals": [{
                    "company_id": company_id, "actions": ["CREATE_COMPANY"],
                }]},
                headers={"X-CSRF-Token": self._csrf()},
            )
        self.assertEqual(synced.status_code, 200)
        self.assertEqual(service.check_calls, [[company_id]])
        self.assertEqual(service.sync_calls[0][0], 4)
        self.assertEqual(service.sync_calls[0][1][0]["actions"], ["CREATE_COMPANY"])

    def test_companies_page_contains_hubspot_status_and_preview_controls(self) -> None:
        self.db.create_company(Lead(name="Acme", market="Texas"))
        page = self.client.get("/companies")
        self.assertIn(b"data-hubspot-check", page.data)
        self.assertIn(b"data-hubspot-preview", page.data)
        self.assertIn(b"hubspot-status", page.data)
        self.assertIn(b'data-label-company-changes', page.data)
        self.assertIn(b'data-label-contact-changes', page.data)

    def test_settings_describes_both_supported_bearer_credentials(self) -> None:
        page = self.client.get("/language/en?next=/settings", follow_redirects=True)
        self.assertIn(b"Access Token / Service Key", page.data)
        self.assertIn(b"Service Keys and Private App access tokens", page.data)


if __name__ == "__main__":
    unittest.main()
