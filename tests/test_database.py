from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leadharbor.database import Database
from leadharbor.models import Lead


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_task_and_lead_persistence(self) -> None:
        task_id = self.db.create_task("retail builder", "Texas", "osm", "", 10, False)
        self.db.update_task(task_id, status="running")
        self.db.save_leads([
            Lead(
                name="Acme Builders",
                market="Texas",
                company_type="Builder",
                website="https://acme.example",
                email="info@acme.example",
                score=80,
                source="test",
            )
        ], task_id)

        self.assertEqual(self.db.get_task(task_id)["status"], "running")
        companies = self.db.list_companies(min_score=70)
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["name"], "Acme Builders")
        self.assertEqual(companies[0]["market"], "Texas")
        self.assertEqual(companies[0]["company_type"], "Builder")
        self.assertEqual(self.db.stats()["contactable"], 1)

    def test_duplicate_domain_updates_existing_company(self) -> None:
        task_id = self.db.create_task("builder", "US", "osm", "", 10, False)
        self.db.save_leads([Lead(name="Acme", market="Iowa", website="https://www.acme.example", score=20)], task_id)
        self.db.save_leads([Lead(name="Acme Builders", market="Iowa", website="https://acme.example/about", phone="123", score=60)], task_id)
        companies = self.db.list_companies()
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["score"], 60)
        self.assertEqual(companies[0]["phone"], "123")

    def test_repeated_discovery_preserves_all_source_evidence(self) -> None:
        task_id = self.db.create_task("cabinet builder", "Texas", "brave", "", 10, False)
        self.db.save_leads([Lead(
            name="Acme Builders", market="Texas", website="https://acme.example",
            source="Brave Search", source_url="https://search.example/acme",
            matched_keywords="builder",
        )], task_id)
        self.db.save_leads([Lead(
            name="Acme Builders", market="Texas", website="https://acme.example/contact",
            source="OpenStreetMap", source_url="https://maps.example/acme",
            matched_keywords="cabinet",
        )], task_id)

        company = self.db.list_companies()[0]
        self.assertIn("Brave Search", company["source"])
        self.assertIn("OpenStreetMap", company["source"])
        self.assertIn("https://search.example/acme", company["source_url"])
        self.assertIn("https://maps.example/acme", company["source_url"])
        self.assertIn("builder", company["matched_keywords"])
        self.assertIn("cabinet", company["matched_keywords"])

    def test_directory_contact_enriches_old_name_only_row(self) -> None:
        task_id = self.db.create_task("builder", "US", "association", "rca", 10, False)
        self.db.save_leads([Lead(name="Winkel Construction, Inc.", market="Florida")], task_id)
        self.db.save_leads([Lead(
            name="Winkel Construction, Inc.",
            website="https://www.winkel-construction.com",
            contact_first_name="Richard",
            contact_last_name="Winkel",
            email="rickw@winkel-construction.com",
            phone="352-860-0500",
            market="Florida",
        )], task_id)
        companies = self.db.list_companies()
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["contact_first_name"], "Richard")
        self.assertEqual(companies[0]["email"], "rickw@winkel-construction.com")

    def test_removes_known_empty_association_button_false_positive(self) -> None:
        task_id = self.db.create_task("builder", "US", "association", "rca", 10, False)
        self.db.save_leads([Lead(name="Find A Contractor", market="Florida")], task_id)
        self.assertEqual(len(self.db.list_companies()), 1)
        Database(self.db.path)
        self.assertEqual(len(self.db.list_companies()), 0)

    def test_removes_existing_procore_directory_false_positive(self) -> None:
        company_id = self.db.create_company(Lead(
            name="Find General Contractors in Florida", market="Florida",
            website="https://network.procore.com/us/fl?types=general-contractors",
            source_url="https://network.procore.com/us/fl?types=general-contractors",
        ))
        self.assertIsNotNone(self.db.get_company(company_id))

        restarted = Database(self.db.path)

        self.assertIsNone(restarted.get_company(company_id))

    def test_recovers_queued_and_running_tasks_after_restart(self) -> None:
        queued_id = self.db.create_task("queued", "US", "osm", "", 10, False)
        running_id = self.db.create_task("running", "US", "osm", "", 10, False)
        self.db.update_task(running_id, status="running", started_at="2026-01-01T00:00:00+00:00")
        self.assertEqual(self.db.recover_interrupted_tasks(), 2)
        for task_id in (queued_id, running_id):
            task = self.db.get_task(task_id)
            self.assertEqual(task["status"], "interrupted")
            self.assertIn("程序在任务完成前关闭", task["error_message"])
            self.assertTrue(task["finished_at"])
        self.assertEqual(self.db.stats()["running"], 0)

    def test_zero_result_completed_association_is_corrected_to_failed(self) -> None:
        task_id = self.db.create_task("member", "US", "association", "rca", 100, False)
        self.db.update_task(task_id, status="completed", result_count=0)

        restarted = Database(self.db.path)
        task = restarted.get_task(task_id)

        self.assertEqual(task["status"], "failed")
        self.assertIn("没有导入任何成员", task["error_message"])

    def test_scoring_version_recalculates_existing_companies_once(self) -> None:
        company_id = self.db.create_company(Lead(
            name="Texas Cabinet Builders", market="Austin, TX", company_type="Builder",
            website="https://cabinet.example", email="sales@cabinet.example",
            phone="512-555-0100", contact_first_name="Ana", contact_last_name="Diaz",
            score=1,
        ))
        with self.db.connect() as conn:
            conn.execute("DELETE FROM app_metadata WHERE key = 'scoring_version'")

        restarted = Database(self.db.path)

        self.assertEqual(restarted.get_company(company_id)["score"], 75)

    def test_manual_company_create_update_and_delete(self) -> None:
        company_id = self.db.create_company(Lead(
            name="Manual Builders", market="Austin, TX", company_type="Builder", website="https://manual.example",
            email="hello@manual.example", score=25,
        ))
        company = self.db.get_company(company_id)
        self.assertEqual(company["name"], "Manual Builders")
        self.assertEqual(company["company_type"], "Builder")

        updated = Lead(
            name="Manual Construction", market="Dallas, TX", company_type="Contractor",
            website="https://manual.example", phone="555-0100", score=70,
        )
        self.assertTrue(self.db.update_company(company_id, updated))
        company = self.db.get_company(company_id)
        self.assertEqual(company["market"], "Texas")
        self.assertEqual(company["address"], "Dallas, TX")
        self.assertEqual(company["phone"], "555-0100")
        self.assertEqual(company["score"], 70)
        self.assertTrue(self.db.delete_company(company_id))
        self.assertIsNone(self.db.get_company(company_id))

    def test_scoring_settings_persist_and_recalculate_existing_companies(self) -> None:
        company_id = self.db.create_company(Lead(
            name="Iowa Cabinet Builder", market="Iowa", company_type="Builder",
            description="Cabinet installation", score=1,
        ))
        weights = self.db.get_scoring_weights()
        weights["icp_business_type"] = 35
        weights["active_opportunity"] = 15
        self.assertEqual(sum(weights.values()), 120)

        self.assertEqual(self.db.set_scoring_weights(weights), 1)

        self.assertEqual(self.db.get_scoring_weights(), weights)
        self.assertEqual(self.db.get_company(company_id)["score"], 55)

    def test_task_history_delete_blocks_active_task(self) -> None:
        completed = self.db.create_task("done", "US", "osm", "", 10, False)
        active = self.db.create_task("active", "US", "osm", "", 10, False)
        self.db.update_task(completed, status="completed")
        self.assertTrue(self.db.delete_task(completed))
        self.assertFalse(self.db.delete_task(active))
        self.assertIsNone(self.db.get_task(completed))
        self.assertIsNotNone(self.db.get_task(active))

    def test_task_cancel_and_retry_lifecycle(self) -> None:
        queued = self.db.create_task("builder", "Texas", "keyword", "", 20, True)
        self.assertEqual(self.db.request_task_cancel(queued), "cancelled")
        self.assertEqual(self.db.get_task(queued)["status"], "cancelled")
        retry_id = self.db.retry_task(queued)
        self.assertIsNotNone(retry_id)
        self.assertEqual(self.db.get_task(retry_id)["status"], "queued")

        self.db.update_task(retry_id, status="running", progress=42, progress_message="processing:2:5")
        self.assertEqual(self.db.request_task_cancel(retry_id), "cancelling")
        self.assertEqual(self.db.get_task(retry_id)["progress"], 42)

    def test_database_backup_and_restore_round_trip(self) -> None:
        original_id = self.db.create_company(Lead(name="Backup Builder", market="Texas"))
        backup = Path(self.temp_dir.name) / "backup.db"
        self.db.create_backup(backup)
        added_id = self.db.create_company(Lead(name="Added Later", market="Florida"))
        self.assertIsNotNone(self.db.get_company(added_id))

        self.db.restore_backup(backup)

        self.assertIsNotNone(self.db.get_company(original_id))
        self.assertIsNone(self.db.get_company(added_id))

    def test_duplicate_groups_merge_contacts_sources_and_validity(self) -> None:
        keep_id = self.db.create_company(Lead(
            name="Acme Builders", market="Texas", website="https://acme-one.example",
            email="sales@acme.example", source="Brave Search", score=80,
        ))
        remove_id = self.db.create_company(Lead(
            name="Acme Construction", market="Texas", website="https://acme-two.example",
            email="sales@acme.example", phone="555-0100", source="Association", score=60,
        ))
        self.db.update_contact_status(keep_id, "valid", "unchecked", "Email confirmed")
        self.db.update_contact_status(remove_id, "unchecked", "invalid", "Phone disconnected")
        groups = self.db.find_duplicate_groups()
        self.assertEqual(len(groups), 1)
        self.assertIn("email", groups[0]["reasons"])

        self.assertTrue(self.db.merge_companies(keep_id, remove_id))

        merged = self.db.get_company(keep_id)
        self.assertIsNone(self.db.get_company(remove_id))
        self.assertEqual(merged["phone"], "555-0100")
        self.assertIn("Brave Search", merged["source"])
        self.assertIn("Association", merged["source"])
        self.assertEqual(merged["email_status"], "valid")
        self.assertEqual(merged["phone_status"], "invalid")
        self.assertIn("Email confirmed", merged["contact_notes"])

    def test_original_database_import_marks_duplicates_and_only_fills_blanks(self) -> None:
        existing_id = self.db.create_company(Lead(
            name="Acme Builders", market="Texas", website="https://acme.example",
            email="existing@acme.example", score=40,
        ))
        duplicate = Lead(
            name="Acme Construction", market="Florida", website="https://www.acme.example/about",
            email="replacement@acme.example", phone="555-0100", scale="~180 homes", score=90,
        )
        new_company = Lead(name="Coastal Contractors", market="Louisiana", phone="555-0200")
        match, reason = self.db.find_duplicate_company(duplicate)
        self.assertEqual(match["id"], existing_id)
        self.assertEqual(reason, "website")
        batch_id = self.db.create_database_import_batch("old-database.csv", [
            {
                "lead": duplicate, "row_status": "duplicate", "match_company_id": existing_id,
                "duplicate_reason": reason, "missing_fields": ["address"],
            },
            {
                "lead": new_company, "row_status": "new", "match_company_id": None,
                "duplicate_reason": "", "missing_fields": ["email", "website"],
            },
        ])

        batch = self.db.get_database_import_batch(batch_id)
        self.assertEqual(batch["duplicate_count"], 1)
        self.assertEqual(batch["missing_count"], 2)
        created, merged = self.db.confirm_database_import(batch_id)
        self.assertEqual((created, merged), (1, 1))

        existing = self.db.get_company(existing_id)
        self.assertEqual(existing["market"], "Texas")
        self.assertEqual(existing["email"], "existing@acme.example")
        self.assertEqual(existing["phone"], "555-0100")
        self.assertEqual(existing["scale"], "~180 homes")
        self.assertEqual(existing["score"], 90)
        self.assertEqual(len(self.db.list_companies()), 2)
        with self.assertRaises(ValueError):
            self.db.confirm_database_import(batch_id)


if __name__ == "__main__":
    unittest.main()
