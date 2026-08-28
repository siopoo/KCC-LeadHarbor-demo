from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from web_app import create_app
from leadharbor.database import Database
from leadharbor.models import Lead
from leadharbor.scoring import score_lead


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        app = create_app(Path(self.temp_dir.name) / "web.db")
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dashboard_and_companies_render(self) -> None:
        dashboard = self.client.get("/")
        companies = self.client.get("/companies")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("KCC LeadHarbor".encode(), dashboard.data)
        self.assertEqual(companies.status_code, 200)
        self.assertIn("企业线索库".encode("utf-8"), companies.data)
        self.assertIn("关键词 / 地图搜索".encode("utf-8"), dashboard.data)
        self.assertIn("协会资料导入".encode("utf-8"), dashboard.data)
        self.assertIn(b'/tasks/keyword', dashboard.data)
        self.assertIn(b'/tasks/association/pdf/preview', dashboard.data)
        self.assertIn(b'data-discovery-tabs', dashboard.data)
        self.assertIn(b'data-discovery-tab="keyword"', dashboard.data)
        self.assertIn(b'data-discovery-tab="association"', dashboard.data)
        self.assertIn(b'data-discovery-pane="association" hidden', dashboard.data)

    @patch("web_app.EXECUTOR.submit")
    def test_keyword_and_association_create_separate_tasks(self, submit) -> None:
        keyword = self.client.post("/tasks/keyword", data={
            "keyword": "retail builder", "location": "Texas", "limit": "20",
        })
        association = self.client.post("/tasks/association", data={
            "association": "rca", "limit": "100",
        })
        self.assertEqual(keyword.status_code, 302)
        self.assertEqual(association.status_code, 302)
        tasks = self.client.application.config["DATABASE"].list_tasks()
        self.assertEqual([task["source"] for task in tasks], ["association", "keyword"])
        self.assertEqual(tasks[0]["requested_limit"], 10_000)
        self.assertEqual(submit.call_count, 2)

    @patch("web_app.EXECUTOR.submit")
    def test_location_is_limited_to_alphabetical_business_states(self, submit) -> None:
        dashboard = self.client.get("/")
        states = [
            "Florida", "Illinois", "Indiana", "Iowa", "Louisiana",
            "Michigan", "Minnesota", "Mississippi", "Ohio", "Texas", "Wisconsin",
        ]
        positions = [dashboard.data.index(f'value="{state}"'.encode()) for state in states]
        self.assertEqual(positions, sorted(positions))

        invalid = self.client.post("/tasks/keyword", data={
            "keyword": "retail builder", "location": "California", "limit": "20",
        }, follow_redirects=True)
        self.assertIn("地区不在当前业务范围内".encode(), invalid.data)
        self.assertEqual(submit.call_count, 0)

        valid = self.client.post("/tasks/keyword", data={
            "keyword": "retail builder", "location": "texas", "limit": "20",
        })
        self.assertEqual(valid.status_code, 302)
        task = self.client.application.config["DATABASE"].list_tasks()[0]
        self.assertEqual(task["location"], "Texas")
        self.assertEqual(submit.call_count, 1)

    def test_association_form_imports_complete_directory(self) -> None:
        dashboard = self.client.get("/")
        self.assertIn("协会来源".encode(), dashboard.data)
        self.assertIn(b'data-association-source', dashboard.data)
        self.assertIn(b'Retail Contractors Association (RCA)', dashboard.data)
        self.assertNotIn(b'class="association-source-card"', dashboard.data)
        self.assertNotIn("已导入协会（".encode(), dashboard.data)
        self.assertNotIn(b'name="limit" type="number" min="1" max="500" value="100"', dashboard.data)
        self.assertIn(b'/tasks/association/upload', dashboard.data)
        self.assertIn(b'/association-template.csv', dashboard.data)

    def test_completed_association_remains_available_without_duplicate_status_module(self) -> None:
        db = self.client.application.config["DATABASE"]
        task_id = db.create_task(
            "retail contractor", "United States", "association", "rca", 100, False,
        )
        db.update_task(task_id, status="completed", result_count=80)

        dashboard = self.client.get("/")

        self.assertNotIn("已导入协会（".encode(), dashboard.data)
        self.assertNotIn(b"<strong>retail contractor</strong>", dashboard.data)
        self.assertIn(b'data-association-source', dashboard.data)

    def test_active_and_completed_associations_are_counted_once_by_source(self) -> None:
        db = self.client.application.config["DATABASE"]
        old_id = db.create_task("member", "United States", "association", "rca", 100, False)
        db.update_task(old_id, status="completed", result_count=75)
        current_id = db.create_task("member", "United States", "association", "rca", 100, False)
        db.update_task(current_id, status="completed", result_count=80)
        csv_id = db.create_task(
            "member", "Coastal Association.csv", "association", "csv:C:/imports/coastal.csv", 100, False,
        )
        db.update_task(csv_id, status="running", result_count=0)

        dashboard = self.client.get("/")

        self.assertNotIn("已导入协会（".encode(), dashboard.data)
        self.assertIn(b"Retail Contractors Association (RCA)", dashboard.data)
        self.assertIn(b"Coastal Association.csv", dashboard.data)

    def test_interrupted_refresh_does_not_forget_an_earlier_completed_import(self) -> None:
        db = self.client.application.config["DATABASE"]
        completed_id = db.create_task("member", "United States", "association", "rca", 100, False)
        db.update_task(completed_id, status="completed", result_count=80)
        interrupted_id = db.create_task("member", "United States", "association", "rca", 100, False)
        db.update_task(interrupted_id, status="interrupted", result_count=0)

        dashboard = self.client.get("/")

        self.assertNotIn("已导入协会（".encode(), dashboard.data)
        self.assertIn(b'data-association-source', dashboard.data)

    @patch("web_app.EXECUTOR.submit")
    def test_other_association_csv_can_be_uploaded(self, submit) -> None:
        app_data = Path(self.temp_dir.name) / "app-data"
        csv_data = (
            "Company,Market,Contact First Name,Contact Last Name,Contact Info,Phone Number (if available),Website\n"
            "Coastal Builders,Alabama,Ana,Diaz,ana@example.com,555-0100,https://example.com\n"
        ).encode()
        with patch("web_app.app_data_dir", return_value=app_data):
            response = self.client.post(
                "/tasks/association/upload",
                data={
                    "csrf_token": self._csrf(),
                    "association_csv": (BytesIO(csv_data), "coastal-association.csv"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)
        task = self.client.application.config["DATABASE"].list_tasks()[0]
        self.assertEqual(task["source"], "association")
        self.assertEqual(task["requested_limit"], 10_000)
        self.assertTrue(task["association"].startswith("csv:"))
        self.assertTrue(Path(task["association"].removeprefix("csv:")).is_file())
        self.assertEqual(submit.call_count, 1)

    def test_association_csv_template_has_expected_headers(self) -> None:
        response = self.client.get("/association-template.csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"\xef\xbb\xbfCompany,Market,Type"))

    @patch("web_app.EXECUTOR.submit")
    def test_pdf_upload_previews_then_confirms_import(self, submit) -> None:
        app_data = Path(self.temp_dir.name) / "pdf-app-data"
        recognized = [Lead(
            name="Coastal Builders LLC", market="Mobile, AL", email="hello@coastal.example",
        )]
        with patch("web_app.app_data_dir", return_value=app_data), patch(
            "web_app.PdfAssociationSource.discover", return_value=recognized,
        ):
            preview_response = self.client.post(
                "/tasks/association/pdf/preview",
                data={
                    "csrf_token": self._csrf(),
                    "association_name": "Coastal Contractors Association",
                    "association_pdf": (BytesIO(b"%PDF-1.4 test"), "members.pdf"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(preview_response.status_code, 200)
        self.assertIn("协会 PDF 识别预览".encode(), preview_response.data)
        self.assertIn(b"Coastal Builders LLC", preview_response.data)
        db = self.client.application.config["DATABASE"]
        with db.connect() as conn:
            preview_id = conn.execute(
                "SELECT id FROM association_pdf_previews ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        confirmed = self.client.post(
            f"/association-imports/{preview_id}/confirm",
            data={"csrf_token": self._csrf()},
        )
        self.assertEqual(confirmed.status_code, 302)
        task = db.list_tasks()[0]
        self.assertEqual(task["association"], f"pdf:{preview_id}")
        self.assertEqual(submit.call_count, 1)

    def test_pdf_import_requires_valid_csrf(self) -> None:
        response = self.client.post(
            "/tasks/association/pdf/preview",
            data={"csrf_token": "wrong", "association_name": "Bad"},
        )
        self.assertEqual(response.status_code, 400)

    def test_csv_export_has_utf8_bom(self) -> None:
        response = self.client.get("/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"\xef\xbb\xbf"))
        text = response.data.decode("utf-8-sig")
        self.assertTrue(text.startswith(
            "Company,Market,Address,Type,Contact First Name,Contact Last Name,Contact Info,"
            "Phone Number (if available),Signal,Scale,Score"
        ))
        self.assertIn("Score Breakdown,Source,Source URL,Matched Keywords,Updated At", text)

    def test_companies_page_uses_requested_output_columns(self) -> None:
        self.client.application.config["DATABASE"].create_company(Lead(
            name="ABC Homes", market="Chicago, IL", scale="~180 homes", score=94,
        ))
        self.client.get("/language/en?next=/companies")
        response = self.client.get("/companies")
        self.assertIn(b"Contact First Name", response.data)
        self.assertIn(b"Address", response.data)
        self.assertNotIn(b"Office", response.data)
        self.assertIn(b"Illinois", response.data)
        self.assertIn(b"Chicago, IL", response.data)
        self.assertIn(b'data-bulk-form', response.data)
        self.assertIn(b'data-company-select', response.data)
        header = response.data.split(b"</thead>", 1)[0]
        self.assertGreater(header.index(b"data-select-all"), header.index(b"Actions"))
        self.assertIn(b"Signal", response.data)
        self.assertIn(b"Scale", response.data)
        self.assertIn(b"Score", response.data)
        self.assertIn(b"~180 homes", response.data)
        self.assertIn(b">94<", response.data)
        self.assertIn(b'data-table-scrollbar', response.data)
        self.assertIn(b'aria-controls="company-output-table"', response.data)
        self.assertIn(b'row-actions-inner', response.data)

    def test_companies_page_shows_score_details_and_source_evidence(self) -> None:
        lead = Lead(
            name="Evidence Cabinet Builders", market="Austin, TX", company_type="Builder",
            website="https://evidence.example", email="sales@evidence.example",
            phone="512-555-0100", signal="Recent permit for a new community",
            scale="180 homes", source="Brave Search",
            source_url="https://search.example/evidence", matched_keywords="cabinet, builder",
        )
        score_lead(lead, "cabinet builder")
        self.client.application.config["DATABASE"].create_company(lead)

        response = self.client.get("/companies")

        self.assertEqual(response.status_code, 200)
        self.assertIn("评分明细与来源证据".encode(), response.data)
        self.assertIn("ICP 业务类型".encode(), response.data)
        self.assertIn("来源页面".encode(), response.data)
        self.assertIn(b'https://search.example/evidence', response.data)
        self.assertIn(b'class="evidence-disclosure"', response.data)
        self.assertIn(b"Recent permit for a new community", response.data)

    def test_market_dropdown_filters_table_and_current_export(self) -> None:
        db = self.client.application.config["DATABASE"]
        db.create_company(Lead(name="Chicago Office Lead", market="Chicago, IL"))
        db.create_company(Lead(name="Wisconsin Office Lead", market="Madison, WI"))
        db.create_company(Lead(name="Florida Office Lead", market="Orlando, FL"))
        db.create_company(Lead(name="Unassigned Lead", market="Dallas, TX"))

        page = self.client.get("/companies?market=Wisconsin")
        self.assertIn(b'<select name="market">', page.data)
        self.assertIn(b'value="Wisconsin" selected', page.data)
        self.assertIn(b"Wisconsin Office Lead", page.data)
        self.assertNotIn(b"Chicago Office Lead", page.data)

        exported = self.client.get("/export.csv?market=Florida")
        export_text = exported.data.decode("utf-8-sig")
        self.assertIn("Florida Office Lead", export_text)
        self.assertNotIn("Wisconsin Office Lead", export_text)

    def test_bulk_export_and_delete_selected_companies(self) -> None:
        db = self.client.application.config["DATABASE"]
        chicago_id = db.create_company(Lead(name="Chicago Builder", market="Chicago, IL"))
        florida_id = db.create_company(Lead(name="Florida Builder", market="Orlando, FL"))

        exported = self.client.post(
            "/companies/bulk/export",
            data={"csrf_token": self._csrf(), "company_ids": [str(florida_id)]},
        )
        self.assertEqual(exported.status_code, 200)
        export_text = exported.data.decode("utf-8-sig")
        self.assertIn("Florida Builder,Florida,\"Orlando, FL\"", export_text)
        self.assertNotIn("Chicago Builder", export_text)

        deleted = self.client.post(
            "/companies/bulk/delete",
            data={"csrf_token": self._csrf(), "company_ids": [str(chicago_id), str(florida_id)]},
            follow_redirects=True,
        )
        self.assertIn("已删除 2 家企业".encode(), deleted.data)
        self.assertEqual(db.list_companies(), [])

    def test_bulk_actions_require_valid_csrf(self) -> None:
        response = self.client.post(
            "/companies/bulk/delete", data={"csrf_token": "wrong", "company_ids": ["1"]},
        )
        self.assertEqual(response.status_code, 400)

    def test_language_switch_persists_for_english_and_spanish(self) -> None:
        english = self.client.get("/language/en?next=/", follow_redirects=True)
        self.assertIn(b"Keyword / Map Search", english.data)
        self.assertIn(b'<html lang="en">', english.data)
        english_companies = self.client.get("/companies")
        self.assertIn(b"Company Lead Database", english_companies.data)

        spanish = self.client.get("/language/es?next=/companies", follow_redirects=True)
        self.assertIn("Base de prospectos empresariales".encode(), spanish.data)
        self.assertIn(b'<html lang="es">', spanish.data)
        spanish_dashboard = self.client.get("/")
        self.assertIn("Búsqueda por palabra / mapa".encode(), spanish_dashboard.data)

    @patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": ""})
    def test_settings_page_saves_masks_and_removes_brave_api_key(self) -> None:
        page = self.client.get("/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn("设置".encode(), page.data)
        self.assertIn(b"Brave Search API", page.data)
        self.assertIn(b"OpenStreetMap", page.data)

        secret = "test-secret-api-key-ABCD"
        saved = self.client.post(
            "/settings",
            data={"csrf_token": self._csrf(), "brave_api_key": secret},
            follow_redirects=True,
        )
        self.assertIn("API 设置已保存".encode(), saved.data)
        self.assertIn("••••ABCD".encode(), saved.data)
        self.assertNotIn(secret.encode(), saved.data)
        self.assertEqual(
            self.client.application.config["DATABASE"].get_setting("brave_search_api_key"),
            secret,
        )
        dashboard = self.client.get("/")
        self.assertIn("搜索引擎：已配置".encode(), dashboard.data)

        removed = self.client.post(
            "/settings",
            data={"csrf_token": self._csrf(), "remove_brave_key": "on"},
            follow_redirects=True,
        )
        self.assertIn("已移除保存的 API 密钥".encode(), removed.data)
        self.assertEqual(
            self.client.application.config["DATABASE"].get_setting("brave_search_api_key"), "",
        )

    def test_settings_update_requires_valid_csrf(self) -> None:
        response = self.client.post(
            "/settings", data={"csrf_token": "wrong", "brave_api_key": "secret"},
        )
        self.assertEqual(response.status_code, 400)

    def test_settings_can_update_and_reset_scoring_rules(self) -> None:
        page = self.client.get("/settings")
        self.assertIn(b'name="score_icp_business_type"', page.data)
        self.assertIn("评分规则".encode(), page.data)
        values = {
            "icp_business_type": 35, "cabinet_relevance": 5,
            "active_opportunity": 20, "scale_potential": 15,
            "contactability": 10, "region_match": 10,
            "association_membership": 5, "office_assignment": 20,
        }
        form = {"csrf_token": self._csrf(), "settings_section": "scoring"}
        form.update({f"score_{key}": str(value) for key, value in values.items()})
        saved = self.client.post("/settings", data=form, follow_redirects=True)
        self.assertIn("评分规则已保存".encode(), saved.data)
        self.assertEqual(
            self.client.application.config["DATABASE"].get_scoring_weights(), values,
        )

        invalid = dict(form)
        invalid["score_icp_business_type"] = "34"
        rejected = self.client.post("/settings", data=invalid, follow_redirects=True)
        self.assertIn("总分必须等于120".encode(), rejected.data)

        reset = self.client.post("/settings", data={
            "csrf_token": self._csrf(), "settings_section": "scoring", "reset_scoring": "1",
        }, follow_redirects=True)
        self.assertIn("已恢复默认值".encode(), reset.data)

    def test_language_redirect_rejects_external_destination(self) -> None:
        response = self.client.get("/language/en?next=https://example.com")
        self.assertEqual(response.headers["Location"], "/")

    def test_validation_flash_uses_selected_language(self) -> None:
        self.client.get("/language/es?next=/")
        response = self.client.post("/tasks/keyword", data={}, follow_redirects=True)
        self.assertIn("Introduce una palabra clave y una ubicación.".encode(), response.data)

    def test_app_startup_marks_stale_task_interrupted(self) -> None:
        path = Path(self.temp_dir.name) / "restart.db"
        db = Database(path)
        task_id = db.create_task("retail contractor", "US", "association", "rca", 50, False)
        db.update_task(task_id, status="running")
        restarted = create_app(path)
        task = restarted.config["DATABASE"].get_task(task_id)
        self.assertEqual(task["status"], "interrupted")

    def test_settings_can_create_and_restore_database_backup(self) -> None:
        db = self.client.application.config["DATABASE"]
        original_id = db.create_company(Lead(name="Original Builder", market="Texas"))
        created = self.client.post(
            "/settings/database/backup", data={"csrf_token": self._csrf()},
            follow_redirects=True,
        )
        self.assertIn("数据库备份已创建".encode(), created.data)
        backups = list((db.path.parent / "backups").glob("leadharbor-backup-*.db"))
        self.assertEqual(len(backups), 1)
        later_id = db.create_company(Lead(name="Later Builder", market="Florida"))

        restored = self.client.post(
            "/settings/database/restore",
            data={
                "csrf_token": self._csrf(),
                "backup_file": (BytesIO(backups[0].read_bytes()), backups[0].name),
            },
            content_type="multipart/form-data", follow_redirects=True,
        )
        self.assertIn("数据库已成功恢复".encode(), restored.data)
        self.assertIsNotNone(db.get_company(original_id))
        self.assertIsNone(db.get_company(later_id))

    @patch("web_app.EXECUTOR.submit")
    def test_task_can_be_cancelled_and_retried(self, submit) -> None:
        db = self.client.application.config["DATABASE"]
        task_id = db.create_task("builder", "Texas", "keyword", "", 10, False)
        cancelled = self.client.post(
            f"/tasks/{task_id}/cancel", data={"csrf_token": self._csrf()},
        )
        self.assertEqual(cancelled.status_code, 302)
        self.assertEqual(db.get_task(task_id)["status"], "cancelled")

        retried = self.client.post(
            f"/tasks/{task_id}/retry", data={"csrf_token": self._csrf()},
        )
        self.assertEqual(retried.status_code, 302)
        self.assertEqual(db.list_tasks()[0]["status"], "queued")
        self.assertEqual(submit.call_count, 1)

    def test_duplicate_management_merges_records(self) -> None:
        db = self.client.application.config["DATABASE"]
        keep_id = db.create_company(Lead(
            name="Alpha Builders", market="Texas", website="https://alpha-one.example",
            email="same@alpha.example", score=80,
        ))
        remove_id = db.create_company(Lead(
            name="Alpha Construction", market="Texas", website="https://alpha-two.example",
            email="same@alpha.example", phone="555-0100", score=60,
        ))
        page = self.client.get("/companies/duplicates")
        self.assertIn("重复项管理".encode(), page.data)
        self.assertIn(b"Alpha Builders", page.data)
        merged = self.client.post(
            "/companies/duplicates/merge",
            data={"csrf_token": self._csrf(), "keep_id": keep_id, "remove_id": remove_id},
            follow_redirects=True,
        )
        self.assertIn("没有发现重复企业".encode(), merged.data)
        self.assertEqual(db.get_company(keep_id)["phone"], "555-0100")
        self.assertIsNone(db.get_company(remove_id))

    def _csrf(self) -> str:
        self.client.get("/companies")
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def test_company_crud_routes(self) -> None:
        csrf = self._csrf()
        created = self.client.post("/companies/new", data={
            "csrf_token": csrf, "name": "Manual Builders", "market": "Austin, TX",
            "company_type": "Builder", "website": "https://manual.example",
            "email": "hello@manual.example", "score": "35",
        })
        self.assertEqual(created.status_code, 302)
        companies = self.client.application.config["DATABASE"].list_companies()
        self.assertEqual(len(companies), 1)
        company_id = companies[0]["id"]

        edited = self.client.post(f"/companies/{company_id}/edit", data={
            "csrf_token": csrf, "name": "Manual Construction", "market": "Dallas, TX",
            "company_type": "Contractor", "website": "https://manual.example",
            "phone": "555-0100", "score": "80", "email_status": "valid",
            "phone_status": "invalid", "contact_notes": "Phone disconnected",
        })
        self.assertEqual(edited.status_code, 302)
        self.assertEqual(self.client.application.config["DATABASE"].get_company(company_id)["phone"], "555-0100")
        updated = self.client.application.config["DATABASE"].get_company(company_id)
        self.assertEqual(updated["email_status"], "valid")
        self.assertEqual(updated["phone_status"], "invalid")
        self.assertEqual(updated["contact_notes"], "Phone disconnected")

        deleted = self.client.post(f"/companies/{company_id}/delete", data={"csrf_token": csrf})
        self.assertEqual(deleted.status_code, 302)
        self.assertIsNone(self.client.application.config["DATABASE"].get_company(company_id))

    def test_company_delete_rejects_invalid_csrf(self) -> None:
        company_id = self.client.application.config["DATABASE"].create_company(
            Lead(name="Protected Company", market="Ohio")
        )
        response = self.client.post(
            f"/companies/{company_id}/delete", data={"csrf_token": "wrong"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(self.client.application.config["DATABASE"].get_company(company_id))

    def test_original_database_upload_previews_duplicates_missing_fields_and_confirms(self) -> None:
        db = self.client.application.config["DATABASE"]
        existing_id = db.create_company(Lead(
            name="Acme Builders", market="Texas", website="https://acme.example",
        ))
        csv_data = (
            "Company,Market,Type,Contact First Name,Contact Last Name,Contact Info,"
            "Phone Number (if available),Website,Address,Signal,Scale,Score\n"
            "Acme Builders,Florida,Builder,Ana,Diaz,ana@acme.example,555-0100,"
            "https://acme.example,,,,90\n"
            "Coastal Contractors,Louisiana,Contractor,,,,555-0200,,,,,70\n"
        ).encode("utf-8")
        preview = self.client.post(
            "/companies/import",
            data={
                "csrf_token": self._csrf(),
                "database_file": (BytesIO(csv_data), "old-database.csv"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("数据库导入预览".encode(), preview.data)
        self.assertIn("重复".encode(), preview.data)
        self.assertIn("缺失资料".encode(), preview.data)
        self.assertIn(b"Acme Builders", preview.data)

        with db.connect() as conn:
            batch_id = conn.execute(
                "SELECT id FROM database_import_batches ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        confirmed = self.client.post(
            f"/companies/import/{batch_id}/confirm",
            data={"csrf_token": self._csrf()},
            follow_redirects=True,
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertIn("新增 1 家".encode(), confirmed.data)
        existing = db.get_company(existing_id)
        self.assertEqual(existing["market"], "Texas")
        self.assertEqual(existing["email"], "ana@acme.example")
        self.assertEqual(len(db.list_companies()), 2)

    def test_original_database_import_requires_valid_csrf(self) -> None:
        response = self.client.post(
            "/companies/import",
            data={
                "csrf_token": "wrong",
                "database_file": (BytesIO(b"Company\nBad"), "bad.csv"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_completed_task_history_can_be_deleted(self) -> None:
        db = self.client.application.config["DATABASE"]
        task_id = db.create_task("done", "US", "osm", "", 10, False)
        db.update_task(task_id, status="completed")
        response = self.client.post(
            f"/tasks/{task_id}/delete", data={"csrf_token": self._csrf()}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.get_task(task_id))


if __name__ == "__main__":
    unittest.main()
