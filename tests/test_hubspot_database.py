from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from leadharbor.database import Database
from leadharbor.models import Lead


class HubSpotDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "test.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_existing_database_is_upgraded_without_losing_company(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.executescript("""
                CREATE TABLE crawl_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL,
                    location TEXT NOT NULL, source TEXT NOT NULL, association TEXT NOT NULL DEFAULT '',
                    requested_limit INTEGER NOT NULL, crawl_websites INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'queued', result_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT '', output_path TEXT NOT NULL DEFAULT '',
                    progress INTEGER NOT NULL DEFAULT 0, progress_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
                );
                CREATE TABLE companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, unique_key TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, market TEXT NOT NULL DEFAULT '', company_type TEXT NOT NULL DEFAULT '',
                    contact_first_name TEXT NOT NULL DEFAULT '', contact_last_name TEXT NOT NULL DEFAULT '',
                    website TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
                    signal TEXT NOT NULL DEFAULT '', scale TEXT NOT NULL DEFAULT '',
                    current_lead_status TEXT NOT NULL DEFAULT 'Unchecked', score INTEGER NOT NULL DEFAULT 0,
                    matched_keywords TEXT NOT NULL DEFAULT '', email_status TEXT NOT NULL DEFAULT 'unchecked',
                    phone_status TEXT NOT NULL DEFAULT 'unchecked', contact_notes TEXT NOT NULL DEFAULT '', task_id INTEGER,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO app_metadata(key, value) VALUES ('scoring_version', '2');
                INSERT INTO companies(
                    unique_key, name, market, website, created_at, updated_at
                ) VALUES ('acme.example', 'Acme Builders', 'Texas', 'https://acme.example', 'old', 'old');
            """)

        db = Database(self.path)
        company = db.list_companies()[0]

        self.assertEqual(company["name"], "Acme Builders")
        self.assertEqual(company["hubspot_company_id"], "")
        self.assertEqual(company["hubspot_sync_status"], "UNCHECKED")
        self.assertEqual(company["contact_phone"], "")
        self.assertEqual(company["city"], "")
        self.assertEqual(company["state"], "Texas")
        self.assertEqual(company["job_title"], "")
        conn = db.connect()
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        finally:
            conn.close()
        self.assertIn("hubspot_check_batches", tables)
        self.assertIn("hubspot_check_results", tables)

    def test_hubspot_relationship_and_check_snapshot_round_trip(self) -> None:
        db = Database(self.path)
        company_id = db.create_company(Lead(
            name="Acme Builders", market="Texas", website="https://acme.example",
            contact_first_name="Ana", contact_last_name="Diaz", email="ana@acme.example",
        ))
        checked = {
            "company_id": company_id,
            "status": "ENRICHABLE",
            "match_reason": "domain",
            "match_confidence": "exact",
            "hubspot_company_id": "hs-company-1",
            "hubspot_contact_id": "hs-contact-1",
            "hubspot_company_updated_at": "2026-01-01T00:00:00Z",
            "hubspot_contact_updated_at": "2026-01-01T00:00:00Z",
            "company_record": {"id": "hs-company-1", "properties": {"phone": ""}},
            "contact_record": {"id": "hs-contact-1", "properties": {"email": "ana@acme.example"}},
            "company_differences": [{"field": "phone", "action": "FILL_MISSING"}],
            "contact_differences": [],
            "error": "",
        }

        batch_id = db.create_hubspot_check_batch([checked])
        batch = db.get_hubspot_check_batch(batch_id)
        result = db.get_hubspot_check_result(batch_id, company_id)

        self.assertEqual(batch["total_count"], 1)
        self.assertEqual(result["status"], "ENRICHABLE")
        self.assertEqual(json.loads(result["company_record_json"])["id"], "hs-company-1")
        local = db.get_company(company_id)
        self.assertEqual(local["hubspot_company_id"], "hs-company-1")
        self.assertEqual(local["hubspot_sync_status"], "ENRICHABLE")
        self.assertTrue(local["hubspot_last_checked_at"])

        db.record_hubspot_sync_result(
            company_id,
            status="SYNCED",
            hubspot_company_id="hs-company-1",
            hubspot_contact_id="hs-contact-1",
            error="",
        )
        synced = db.get_company(company_id)
        self.assertEqual(synced["hubspot_sync_status"], "SYNCED")
        self.assertTrue(synced["hubspot_last_synced_at"])

    def test_failed_sync_keeps_successful_ids_and_safe_error(self) -> None:
        db = Database(self.path)
        company_id = db.create_company(Lead(name="Partial Company", market="Florida"))

        db.record_hubspot_sync_result(
            company_id,
            status="FAILED",
            hubspot_company_id="company-created",
            hubspot_contact_id="",
            error="Association failed",
        )

        company = db.get_company(company_id)
        self.assertEqual(company["hubspot_company_id"], "company-created")
        self.assertEqual(company["hubspot_sync_status"], "FAILED")
        self.assertEqual(company["hubspot_last_error"], "Association failed")

    def test_explicit_company_and_contact_fields_round_trip(self) -> None:
        db = Database(self.path)
        company_id = db.create_company(Lead(
            name="Field Builder", market="Texas", phone="210-555-1000",
            contact_phone="210-555-2000", job_title="Buyer", address="10 Main St",
            city="Austin", state="TX", country="United States", employee_count="42",
        ))
        company = db.get_company(company_id)
        self.assertEqual(company["phone"], "210-555-1000")
        self.assertEqual(company["contact_phone"], "210-555-2000")
        self.assertEqual(company["job_title"], "Buyer")
        self.assertEqual(company["city"], "Austin")
        self.assertEqual(company["state"], "TX")
        self.assertEqual(company["employee_count"], "42")

    def test_local_duplicate_merge_preserves_hubspot_relationship(self) -> None:
        db = Database(self.path)
        keep_id = db.create_company(Lead(
            name="Acme Keep", market="Texas", website="https://keep.example",
            email="same@acme.example",
        ))
        remove_id = db.create_company(Lead(
            name="Acme Remove", market="Texas", website="https://remove.example",
            email="same@acme.example",
        ))
        db.record_hubspot_sync_result(
            remove_id, status="SYNCED", hubspot_company_id="hs-company",
            hubspot_contact_id="hs-contact",
        )

        self.assertTrue(db.merge_companies(keep_id, remove_id))

        merged = db.get_company(keep_id)
        self.assertEqual(merged["hubspot_company_id"], "hs-company")
        self.assertEqual(merged["hubspot_contact_id"], "hs-contact")
        self.assertEqual(merged["hubspot_sync_status"], "SYNCED")


if __name__ == "__main__":
    unittest.main()
