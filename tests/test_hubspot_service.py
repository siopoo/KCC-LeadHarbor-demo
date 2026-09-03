from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leadharbor.database import Database
from leadharbor.hubspot.mapper import map_company_properties, map_contact_properties
from leadharbor.hubspot.sync import HubSpotSyncService
from leadharbor.models import Lead


class FakeObjectAPI:
    def __init__(self, candidates_by_name: dict[str, list[dict]] | None = None):
        self.candidates_by_name = candidates_by_name or {}
        self.get_records: dict[str, dict] = {}
        self.reads: list[tuple[str, str]] = []
        self.writes: list[tuple] = []
        self.next_id = 100
        self.fail_names: set[str] = set()
        self.preloaded: list[list[str]] = []

    def available_properties(self) -> set[str]:
        return {
            "name", "domain", "website", "phone", "address", "city", "state",
            "country", "industry", "numberofemployees", "email", "firstname",
            "lastname", "jobtitle",
        }

    def candidates(self, local: dict, linked_id: str = "") -> list[dict]:
        self.reads.append((str(local.get("name", "")), linked_id))
        if linked_id and linked_id in self.get_records:
            return [self.get_records[linked_id]]
        key = str(local.get("name", "")) or str(local.get("email", ""))
        return list(self.candidates_by_name.get(key, []))

    def get(self, record_id: str) -> dict:
        self.reads.append(("get", record_id))
        return self.get_records[record_id]

    def preload(self, record_ids: list[str]) -> None:
        self.preloaded.append(list(record_ids))

    def create(self, properties: dict) -> dict:
        self.writes.append(("create", properties))
        return {"id": "created", "properties": properties, "updatedAt": "now"}

    def update(self, record_id: str, properties: dict) -> dict:
        self.writes.append(("update", record_id, properties))
        record = self.get_records[record_id]
        record["properties"].update(properties)
        return record

    def create_many(self, items: list[tuple[str, dict]]) -> dict[str, dict]:
        self.writes.append(("create_many", items))
        outcomes: dict[str, dict] = {}
        for key, properties in items:
            if properties.get("name") in self.fail_names:
                outcomes[key] = {"error": "HubSpot rejected a field value as invalid."}
                continue
            self.next_id += 1
            record = {
                "id": str(self.next_id), "properties": dict(properties),
                "updatedAt": "created-now",
            }
            self.get_records[record["id"]] = record
            outcomes[key] = {"record": record}
        return outcomes

    def update_many(self, items: list[tuple[str, str, dict]]) -> dict[str, dict]:
        self.writes.append(("update_many", items))
        outcomes: dict[str, dict] = {}
        for key, record_id, properties in items:
            record = self.get_records[record_id]
            record["properties"].update(properties)
            outcomes[key] = {"record": record}
        return outcomes


class FakeAssociations:
    def __init__(self):
        self.writes: list[tuple[str, str]] = []
        self.fail = False

    def associate_contact_company(self, contact_id: str, company_id: str) -> None:
        self.writes.append((contact_id, company_id))

    def associate_many(self, items: list[tuple[str, str, str]]) -> dict[str, dict]:
        for key, contact_id, company_id in items:
            self.writes.append((contact_id, company_id))
        return {
            key: ({"error": "Association failed"} if self.fail else {"ok": True})
            for key, _, _ in items
        }


class HubSpotMapperTests(unittest.TestCase):
    def test_mapping_only_returns_clean_known_non_empty_properties(self) -> None:
        company = map_company_properties({
            "id": 9, "name": "Acme Builders", "website": "https://www.acme.example/about",
            "phone": " 512-555-0100 ", "address": "10 Main St, Austin, TX",
            "market": "Texas", "country": "United States", "company_type": "Builder",
            "score": 88, "source": "Brave Search", "description": "",
        }, available={"name", "domain", "website", "phone", "address", "state", "country", "industry", "leadharbor_company_id", "leadharbor_score"})
        self.assertEqual(company["domain"], "acme.example")
        self.assertEqual(company["name"], "Acme Builders")
        self.assertEqual(company["leadharbor_company_id"], "9")
        self.assertEqual(company["leadharbor_score"], "88")
        self.assertNotIn("description", company)
        self.assertNotIn("leadharbor_source", company)

    def test_contact_mapping_requires_real_person_data(self) -> None:
        generic = map_contact_properties({
            "email": "", "contact_first_name": "", "contact_last_name": "",
            "phone": "512-555-0100",
        }, available={"email", "firstname", "lastname", "phone"})
        self.assertEqual(generic, {})
        person = map_contact_properties({
            "id": 3, "email": " ANA@EXAMPLE.COM ", "contact_first_name": "Ana",
            "contact_last_name": "Diaz", "phone": "512-555-0100",
        }, available={"email", "firstname", "lastname", "phone", "leadharbor_contact_id"})
        self.assertEqual(person["email"], "ana@example.com")
        self.assertEqual(person["firstname"], "Ana")
        self.assertEqual(person["leadharbor_contact_id"], "3")


class HubSpotCheckServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _company(self, name: str, **values) -> int:
        return self.db.create_company(Lead(name=name, market="Texas", **values))

    def test_check_classifies_records_and_never_writes_to_hubspot(self) -> None:
        new_id = self._company("New Builder", website="https://new.example")
        duplicate_id = self._company("Existing Builder", website="https://existing.example")
        enrich_id = self._company(
            "Enrich Builder", website="https://enrich.example", phone="512-555-0199",
        )
        conflict_id = self._company("Similar Builder", website="")
        companies = FakeObjectAPI({
            "Existing Builder": [{
                "id": "hs-duplicate", "updatedAt": "u1",
                "properties": {"name": "Existing Builder", "domain": "existing.example", "website": "https://existing.example", "state": "Texas", "industry": "Builder"},
            }],
            "Enrich Builder": [{
                "id": "hs-enrich", "updatedAt": "u2",
                "properties": {"name": "Enrich Builder", "domain": "enrich.example", "website": "https://enrich.example", "phone": "", "state": "Texas", "industry": "Builder"},
            }],
            "Similar Builder": [{
                "id": "hs-possible", "updatedAt": "u3",
                "properties": {"name": "Similar Builder LLC", "state": "Florida"},
            }],
        })
        contacts = FakeObjectAPI()
        associations = FakeAssociations()
        service = HubSpotSyncService(self.db, companies, contacts, associations)

        checked = service.check([new_id, duplicate_id, enrich_id, conflict_id])

        self.assertEqual(checked["summary"], {
            "new": 1, "duplicate": 1, "enrichable": 1, "conflict": 1, "failed": 0,
        })
        statuses = {item["company_id"]: item["status"] for item in checked["results"]}
        self.assertEqual(statuses[new_id], "NEW")
        self.assertEqual(statuses[duplicate_id], "DUPLICATE")
        self.assertEqual(statuses[enrich_id], "ENRICHABLE")
        self.assertEqual(statuses[conflict_id], "CONFLICT")
        enrich = next(item for item in checked["results"] if item["company_id"] == enrich_id)
        self.assertIn("phone", {
            item["field"] for item in enrich["company_differences"]
            if item["action"] == "FILL_MISSING"
        })
        self.assertEqual(companies.writes, [])
        self.assertEqual(contacts.writes, [])
        self.assertEqual(associations.writes, [])
        self.assertIsNotNone(self.db.get_hubspot_check_batch(checked["batch_id"]))

    def test_check_reuses_local_hubspot_id_before_searching(self) -> None:
        company_id = self._company("Known Builder", website="https://known.example")
        self.db.record_hubspot_sync_result(
            company_id, status="SYNCED", hubspot_company_id="hs-known",
        )
        companies = FakeObjectAPI()
        companies.get_records["hs-known"] = {
            "id": "hs-known", "updatedAt": "u1",
            "properties": {"name": "Known Builder", "domain": "known.example", "website": "https://known.example", "state": "Texas", "industry": "Builder"},
        }
        service = HubSpotSyncService(self.db, companies, FakeObjectAPI(), FakeAssociations())

        result = service.check([company_id])["results"][0]

        self.assertEqual(result["status"], "DUPLICATE")
        self.assertEqual(result["match_reason"], "local_hubspot_id")
        self.assertEqual(companies.preloaded, [["hs-known"]])
        self.assertEqual(companies.reads[0], ("Known Builder", "hs-known"))

    def test_sync_creates_real_contact_associates_and_is_idempotent(self) -> None:
        company_id = self._company(
            "New Contact Builder", website="https://new-contact.example",
            contact_first_name="Ana", contact_last_name="Diaz",
            email="ana@new-contact.example", phone="512-555-0100",
        )
        companies = FakeObjectAPI()
        contacts = FakeObjectAPI()
        associations = FakeAssociations()
        service = HubSpotSyncService(self.db, companies, contacts, associations)
        checked = service.check([company_id])

        synced = service.sync(checked["batch_id"], [{
            "company_id": company_id,
            "actions": ["CREATE_COMPANY", "CREATE_CONTACT", "ASSOCIATE_CONTACT_COMPANY"],
        }])

        self.assertEqual(synced["summary"], {"success": 1, "failed": 0, "skipped": 0, "conflict": 0})
        local = self.db.get_company(company_id)
        self.assertTrue(local["hubspot_company_id"])
        self.assertTrue(local["hubspot_contact_id"])
        self.assertEqual(associations.writes, [(local["hubspot_contact_id"], local["hubspot_company_id"])])
        self.assertEqual(companies.writes[0][0], "create_many")
        self.assertEqual(contacts.writes[0][0], "create_many")

        repeated = service.sync(checked["batch_id"], [{
            "company_id": company_id,
            "actions": ["CREATE_COMPANY", "CREATE_CONTACT"],
        }])
        self.assertEqual(repeated["results"][0]["status"], "SYNCED")
        self.assertEqual(len(companies.writes), 1)
        self.assertEqual(len(contacts.writes), 1)

    def test_sync_enriches_only_missing_fields(self) -> None:
        company_id = self._company(
            "Enrich Safe Builder", website="https://safe.example", phone="512-555-0199",
        )
        remote = {
            "id": "hs-safe", "updatedAt": "preview-version",
            "properties": {
                "name": "Enrich Safe Builder", "domain": "safe.example",
                "website": "https://safe.example", "state": "Texas",
                "industry": "Builder", "phone": "",
            },
        }
        companies = FakeObjectAPI({"Enrich Safe Builder": [remote]})
        companies.get_records["hs-safe"] = remote
        service = HubSpotSyncService(self.db, companies, FakeObjectAPI(), FakeAssociations())
        checked = service.check([company_id])

        synced = service.sync(checked["batch_id"], [{
            "company_id": company_id, "actions": ["ENRICH_COMPANY"],
        }])

        self.assertEqual(synced["results"][0]["status"], "SYNCED")
        update_items = companies.writes[0][1]
        self.assertEqual(update_items, [(str(company_id), "hs-safe", {"phone": "512-555-0199"})])

    def test_sync_never_writes_possible_match_without_resolution(self) -> None:
        company_id = self._company("Review Builder")
        remote = {
            "id": "hs-review", "updatedAt": "v1",
            "properties": {"name": "Review Builder LLC", "state": "Florida"},
        }
        companies = FakeObjectAPI({"Review Builder": [remote]})
        service = HubSpotSyncService(self.db, companies, FakeObjectAPI(), FakeAssociations())
        checked = service.check([company_id])

        result = service.sync(checked["batch_id"], [{
            "company_id": company_id, "actions": ["CREATE_COMPANY"],
        }])

        self.assertEqual(result["results"][0]["status"], "CONFLICT")
        self.assertEqual(companies.writes, [])

    def test_sync_rejects_stale_preview_before_update(self) -> None:
        company_id = self._company(
            "Changed Builder", website="https://changed.example", phone="512-555-0188",
        )
        preview = {
            "id": "hs-changed", "updatedAt": "old-version",
            "properties": {"name": "Changed Builder", "domain": "changed.example", "website": "https://changed.example", "state": "Texas", "industry": "Builder", "phone": ""},
        }
        companies = FakeObjectAPI({"Changed Builder": [preview]})
        companies.get_records["hs-changed"] = {
            **preview, "updatedAt": "new-version",
        }
        service = HubSpotSyncService(self.db, companies, FakeObjectAPI(), FakeAssociations())
        checked = service.check([company_id])

        result = service.sync(checked["batch_id"], [{
            "company_id": company_id, "actions": ["ENRICH_COMPANY"],
        }])

        self.assertEqual(result["results"][0]["status"], "RECHECK_REQUIRED")
        self.assertEqual(companies.writes, [])

    def test_sync_reports_partial_batch_failure_without_hiding_success(self) -> None:
        good_id = self._company("Good Builder", website="https://good.example")
        bad_id = self._company("Bad Builder", website="https://bad.example")
        companies = FakeObjectAPI()
        companies.fail_names.add("Bad Builder")
        service = HubSpotSyncService(self.db, companies, FakeObjectAPI(), FakeAssociations())
        checked = service.check([good_id, bad_id])

        result = service.sync(checked["batch_id"], [
            {"company_id": good_id, "actions": ["CREATE_COMPANY"]},
            {"company_id": bad_id, "actions": ["CREATE_COMPANY"]},
        ])

        statuses = {item["company_id"]: item["status"] for item in result["results"]}
        self.assertEqual(statuses[good_id], "SYNCED")
        self.assertEqual(statuses[bad_id], "FAILED")
        self.assertEqual(result["summary"]["success"], 1)
        self.assertEqual(result["summary"]["failed"], 1)

    def test_association_failure_preserves_created_company_and_contact_ids(self) -> None:
        company_id = self._company(
            "Association Builder", website="https://association.example",
            contact_first_name="Sam", contact_last_name="Lee",
            email="sam@association.example",
        )
        companies = FakeObjectAPI()
        contacts = FakeObjectAPI()
        associations = FakeAssociations()
        associations.fail = True
        service = HubSpotSyncService(self.db, companies, contacts, associations)
        checked = service.check([company_id])

        result = service.sync(checked["batch_id"], [{
            "company_id": company_id,
            "actions": ["CREATE_COMPANY", "CREATE_CONTACT", "ASSOCIATE_CONTACT_COMPANY"],
        }])

        self.assertEqual(result["results"][0]["status"], "FAILED")
        local = self.db.get_company(company_id)
        self.assertTrue(local["hubspot_company_id"])
        self.assertTrue(local["hubspot_contact_id"])
        self.assertEqual(local["hubspot_last_error"], "Association failed")

    def test_existing_contact_is_enriched_and_associated_without_overwrite(self) -> None:
        company_id = self._company(
            "Contact Enrich Builder", website="https://contact-enrich.example",
            contact_first_name="Ana", contact_last_name="Diaz",
            email="ana@contact-enrich.example", phone="512-555-0144",
        )
        remote_company = {
            "id": "hs-company", "updatedAt": "company-v1",
            "properties": {
                "name": "Contact Enrich Builder", "domain": "contact-enrich.example",
                "website": "https://contact-enrich.example", "state": "Texas",
                "industry": "Builder",
            },
        }
        remote_contact = {
            "id": "hs-contact", "updatedAt": "contact-v1",
            "properties": {
                "email": "ana@contact-enrich.example", "firstname": "Ana",
                "lastname": "Diaz", "phone": "",
            },
        }
        companies = FakeObjectAPI({"Contact Enrich Builder": [remote_company]})
        companies.get_records["hs-company"] = remote_company
        contacts = FakeObjectAPI({"Contact Enrich Builder": [remote_contact]})
        contacts.get_records["hs-contact"] = remote_contact
        associations = FakeAssociations()
        service = HubSpotSyncService(self.db, companies, contacts, associations)
        checked = service.check([company_id])
        self.assertEqual(checked["results"][0]["status"], "ENRICHABLE")

        result = service.sync(checked["batch_id"], [{
            "company_id": company_id,
            "actions": ["ENRICH_CONTACT", "ASSOCIATE_CONTACT_COMPANY"],
        }])

        self.assertEqual(result["results"][0]["status"], "SYNCED")
        self.assertEqual(
            contacts.writes[0][1],
            [(str(company_id), "hs-contact", {"phone": "512-555-0144"})],
        )
        self.assertEqual(associations.writes, [("hs-contact", "hs-company")])

    def test_exact_match_conflict_overwrites_only_explicitly_approved_field(self) -> None:
        company_id = self._company(
            "Override Builder", website="https://override.example", phone="512-555-0200",
        )
        remote = {
            "id": "hs-override", "updatedAt": "v1",
            "properties": {
                "name": "Override Builder", "domain": "override.example",
                "website": "https://override.example", "state": "Texas",
                "industry": "Builder", "phone": "512-555-0100",
            },
        }
        companies = FakeObjectAPI({"Override Builder": [remote]})
        companies.get_records["hs-override"] = remote
        service = HubSpotSyncService(self.db, companies, FakeObjectAPI(), FakeAssociations())
        checked = service.check([company_id])
        self.assertEqual(checked["results"][0]["status"], "CONFLICT")

        result = service.sync(checked["batch_id"], [{
            "company_id": company_id,
            "actions": ["ENRICH_COMPANY"],
            "company_overwrite_fields": ["phone"],
        }])

        self.assertEqual(result["results"][0]["status"], "SYNCED")
        self.assertEqual(
            companies.writes[0][1],
            [(str(company_id), "hs-override", {"phone": "512-555-0200"})],
        )


if __name__ == "__main__":
    unittest.main()
