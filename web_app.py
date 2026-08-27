from __future__ import annotations

import csv
import hmac
import io
import logging
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from leadharbor.associations import (
    ASSOCIATION_PRESETS, AssociationSource, PdfAssociationSource, RcaDirectorySource,
)
from leadharbor.classification import TARGET_STATE_NAMES, prepare_output_fields
from leadharbor.database import Database, utc_now
from leadharbor.database_import import missing_fields, parse_database_file
from leadharbor.i18n import DEFAULT_LANGUAGE, LANGUAGES, translate
from leadharbor.models import Lead
from leadharbor.net import domain_key, normalize_url
from leadharbor.pipeline import LeadPipeline
from leadharbor.search import BraveSearchSource
from leadharbor.scoring import DEFAULT_SCORING_WEIGHTS, SCORING_TOTAL, score_lead
from leadharbor.sources import OpenStreetMapSource
from leadharbor.storage import app_data_dir, resource_path

LOG = logging.getLogger(__name__)
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="leadharbor-crawler")
BUSINESS_STATES = TARGET_STATE_NAMES
ASSOCIATION_IMPORT_LIMIT = 10_000
MAX_ASSOCIATION_CSV_BYTES = 5 * 1024 * 1024
MAX_ASSOCIATION_PDF_BYTES = 20 * 1024 * 1024
MAX_DATABASE_IMPORT_BYTES = 10 * 1024 * 1024
ASSOCIATION_LABELS = {"rca": "Retail Contractors Association (RCA)"}
BRAVE_API_SETTING = "brave_search_api_key"


def brave_api_key(db: Database) -> str:
    return db.get_setting(BRAVE_API_SETTING).strip() or os.getenv("BRAVE_SEARCH_API_KEY", "").strip()


def create_app(database_path: Path | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(resource_path("templates")),
        static_folder=str(resource_path("static")),
    )
    app.config["SECRET_KEY"] = os.getenv("KCC_LEADHARBOR_SECRET", "local-desktop-app")
    database = Database(database_path or app_data_dir() / "leadharbor.db")
    database.recover_interrupted_tasks()
    app.config["DATABASE"] = database

    @app.before_request
    def select_language() -> None:
        language = session.get("language", DEFAULT_LANGUAGE)
        g.language = language if language in LANGUAGES else DEFAULT_LANGUAGE

    @app.context_processor
    def language_context() -> dict[str, object]:
        def t(key: str, **values: object) -> str:
            return translate(g.language, key, **values)

        def status_label(status: str) -> str:
            return t(f"status_{status}")

        def source_label(source: str) -> str:
            return t(f"source_{source}") if source in {"keyword", "association"} else source

        def company_type_label(company_type: str) -> str:
            key = (company_type or "Other").casefold().replace(" ", "_")
            known = {"contractor", "builder", "remodeler", "dealer", "multifamily", "other"}
            return t(f"type_{key}") if key in known else company_type

        def import_field_label(field: str) -> str:
            keys = {
                "market": "field_market", "company_type": "field_company_type",
                "contact_first_name": "field_first_name", "contact_last_name": "field_last_name",
                "email": "field_email", "phone": "field_phone", "website": "field_website",
                "address": "field_address", "signal": "field_signal", "scale": "field_scale",
            }
            return t(keys.get(field, field))

        def task_title(task: dict) -> str:
            if task.get("source") == "association":
                association = task.get("association", "")
                if association in ASSOCIATION_LABELS:
                    return ASSOCIATION_LABELS[association]
                return task.get("location") or task.get("keyword") or t("source_association")
            return task.get("keyword", "")

        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return {
            "t": t,
            "status_label": status_label,
            "source_label": source_label,
            "company_type_label": company_type_label,
            "import_field_label": import_field_label,
            "task_title": task_title,
            "current_language": g.language,
            "languages": LANGUAGES,
            "csrf_token": session["csrf_token"],
        }

    def valid_csrf() -> bool:
        expected = session.get("csrf_token", "")
        supplied = request.form.get("csrf_token", "")
        return bool(expected and supplied and hmac.compare_digest(expected, supplied))

    def selected_company_ids() -> list[int]:
        ids: list[int] = []
        for value in request.form.getlist("company_ids"):
            try:
                company_id = int(value)
            except ValueError:
                continue
            if company_id > 0:
                ids.append(company_id)
        return sorted(set(ids))

    def company_csv_response(rows: list[dict], filename: str) -> Response:
        fields = [
            "Company", "Market", "Address", "Type", "Contact First Name", "Contact Last Name",
            "Contact Info", "Phone Number (if available)", "Signal", "Scale", "Score",
        ]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "Company": row["name"], "Market": row["market"], "Address": row["address"],
                "Type": row["company_type"], "Contact First Name": row["contact_first_name"],
                "Contact Last Name": row["contact_last_name"], "Contact Info": row["email"],
                "Phone Number (if available)": row["phone"], "Signal": row["signal"],
                "Scale": row["scale"], "Score": row["score"],
            })
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    def company_from_form() -> Lead:
        name = request.form.get("name", "").strip()
        if not name:
            raise ValueError("name required")
        try:
            score = max(0, min(120, int(request.form.get("score", "0"))))
        except ValueError:
            score = 0
        lead = Lead(
            name=name,
            market=request.form.get("market", "").strip(),
            company_type=request.form.get("company_type", "").strip(),
            contact_first_name=request.form.get("contact_first_name", "").strip(),
            contact_last_name=request.form.get("contact_last_name", "").strip(),
            website=normalize_url(request.form.get("website", "").strip()),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            address=request.form.get("address", "").strip(),
            signal=request.form.get("signal", "").strip(),
            scale=request.form.get("scale", "").strip(),
            source="Manual",
            score=score,
        )
        prepare_output_fields(lead)
        if not lead.market:
            raise ValueError("outside target market")
        return lead

    def company_error_key(exc: ValueError) -> str:
        message = str(exc)
        if message in {"name required", "company name is required"}:
            return "flash_company_required"
        if message == "outside target market":
            return "flash_outside_target_market"
        return "flash_company_duplicate"

    @app.route("/language/<code>", methods=["GET"])
    def set_language(code: str):
        if code in LANGUAGES:
            session["language"] = code
        destination = request.args.get("next", "/")
        if not destination.startswith("/") or destination.startswith("//"):
            destination = "/"
        return redirect(destination)

    @app.route("/", methods=["GET"])
    def dashboard():
        db: Database = app.config["DATABASE"]
        return render_template(
            "dashboard.html",
            stats=db.stats(),
            tasks=db.list_tasks(),
            search_ready=bool(brave_api_key(db)),
            business_states=BUSINESS_STATES,
            association_sources=[
                {
                    "key": key,
                    "label": ASSOCIATION_LABELS.get(key, key.upper()),
                    "url": url,
                }
                for key, url in ASSOCIATION_PRESETS.items()
            ],
        )

    @app.route("/tasks/keyword", methods=["POST"])
    def create_keyword_task():
        keyword = request.form.get("keyword", "").strip()
        location = request.form.get("location", "").strip()
        canonical_location = next(
            (state for state in BUSINESS_STATES if state.casefold() == location.casefold()),
            "",
        )
        crawl_websites = request.form.get("crawl_websites") == "on"
        try:
            requested_limit = min(500, max(1, int(request.form.get("limit", "50"))))
        except ValueError:
            requested_limit = 50
        if not keyword or not location:
            flash(translate(g.language, "flash_missing_keyword"), "error")
            return redirect(url_for("dashboard"))
        if not canonical_location:
            flash(translate(g.language, "flash_invalid_location"), "error")
            return redirect(url_for("dashboard"))

        db: Database = app.config["DATABASE"]
        task_id = db.create_task(
            keyword, canonical_location, "keyword", "", requested_limit, crawl_websites
        )
        EXECUTOR.submit(_run_task, db, task_id)
        if brave_api_key(db):
            flash(translate(g.language, "flash_keyword_queued", id=task_id), "success")
        else:
            flash(translate(g.language, "flash_map_queued", id=task_id), "success")
        return redirect(url_for("dashboard"))

    @app.route("/tasks/association", methods=["POST"])
    def create_association_task():
        association = request.form.get("association", "").strip()
        crawl_websites = request.form.get("crawl_websites") == "on"
        if association not in ASSOCIATION_PRESETS:
            flash(translate(g.language, "flash_choose_association"), "error")
            return redirect(url_for("dashboard"))

        db: Database = app.config["DATABASE"]
        task_id = db.create_task(
            "retail contractor", "United States", "association", association,
            ASSOCIATION_IMPORT_LIMIT, crawl_websites,
        )
        EXECUTOR.submit(_run_task, db, task_id)
        flash(translate(g.language, "flash_association_queued", id=task_id), "success")
        return redirect(url_for("dashboard"))

    @app.route("/tasks/association/upload", methods=["POST"])
    def upload_association_task():
        if not valid_csrf():
            abort(400)
        uploaded = request.files.get("association_csv")
        if not uploaded or not uploaded.filename:
            flash(translate(g.language, "flash_choose_csv"), "error")
            return redirect(url_for("dashboard"))
        original_name = Path(uploaded.filename.replace("\\", "/")).name
        if Path(original_name).suffix.casefold() != ".csv":
            flash(translate(g.language, "flash_invalid_csv"), "error")
            return redirect(url_for("dashboard"))
        payload = uploaded.read(MAX_ASSOCIATION_CSV_BYTES + 1)
        if not payload or len(payload) > MAX_ASSOCIATION_CSV_BYTES:
            flash(translate(g.language, "flash_csv_size"), "error")
            return redirect(url_for("dashboard"))
        try:
            headers = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))).fieldnames or []
        except UnicodeDecodeError:
            headers = []
        normalized_headers = {header.strip().casefold() for header in headers if header}
        if not normalized_headers.intersection({"company", "company_name", "name", "公司名称"}):
            flash(translate(g.language, "flash_invalid_csv"), "error")
            return redirect(url_for("dashboard"))

        imports_dir = app_data_dir() / "imports"
        imports_dir.mkdir(parents=True, exist_ok=True)
        stored_path = imports_dir / f"{secrets.token_hex(12)}.csv"
        stored_path.write_bytes(payload)
        crawl_websites = request.form.get("crawl_websites") == "on"
        db: Database = app.config["DATABASE"]
        task_id = db.create_task(
            "association member", original_name, "association", f"csv:{stored_path}",
            ASSOCIATION_IMPORT_LIMIT, crawl_websites,
        )
        EXECUTOR.submit(_run_task, db, task_id)
        flash(translate(g.language, "flash_csv_queued", id=task_id), "success")
        return redirect(url_for("dashboard"))

    @app.route("/association-template.csv", methods=["GET"])
    def association_template():
        fields = [
            "Company", "Market", "Type", "Contact First Name", "Contact Last Name",
            "Contact Info", "Phone Number (if available)", "Website", "Address",
            "Signal", "Scale", "Score",
        ]
        output = io.StringIO()
        csv.writer(output).writerow(fields)
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=association-import-template.csv"},
        )

    @app.route("/tasks/association/pdf/preview", methods=["POST"])
    def preview_association_pdf():
        if not valid_csrf():
            abort(400)
        association_name = request.form.get("association_name", "").strip()[:200]
        pdf_url = normalize_url(request.form.get("pdf_url", "").strip())
        uploaded = request.files.get("association_pdf")
        if not association_name:
            flash(translate(g.language, "flash_association_name_required"), "error")
            return redirect(url_for("dashboard"))
        if not pdf_url and (not uploaded or not uploaded.filename):
            flash(translate(g.language, "flash_choose_pdf_source"), "error")
            return redirect(url_for("dashboard"))

        try:
            if uploaded and uploaded.filename:
                filename = Path(uploaded.filename.replace("\\", "/")).name
                if Path(filename).suffix.casefold() != ".pdf":
                    raise ValueError("not a PDF")
                payload = uploaded.read(MAX_ASSOCIATION_PDF_BYTES + 1)
                source_url = ""
            else:
                payload = PdfAssociationSource.download_url(pdf_url, MAX_ASSOCIATION_PDF_BYTES)
                source_url = pdf_url
            if not payload or len(payload) > MAX_ASSOCIATION_PDF_BYTES or not payload.startswith(b"%PDF"):
                raise ValueError("invalid PDF")
            imports_dir = app_data_dir() / "imports"
            imports_dir.mkdir(parents=True, exist_ok=True)
            stored_path = imports_dir / f"{secrets.token_hex(12)}.pdf"
            stored_path.write_bytes(payload)
            leads = PdfAssociationSource(
                association_name, stored_path, source_url=source_url
            ).discover("association member", association_name, ASSOCIATION_IMPORT_LIMIT)
        except Exception as exc:
            LOG.warning("Association PDF preview failed: %s", exc)
            flash(translate(g.language, "flash_invalid_pdf"), "error")
            return redirect(url_for("dashboard"))
        if not leads:
            flash(translate(g.language, "flash_pdf_no_members"), "error")
            return redirect(url_for("dashboard"))

        db: Database = app.config["DATABASE"]
        preview_id = db.create_pdf_preview(
            association_name, stored_path, source_url, len(leads)
        )
        return render_template(
            "association_preview.html",
            preview_id=preview_id,
            association_name=association_name,
            leads=leads[:50],
            total_count=len(leads),
        )

    @app.route("/association-imports/<int:preview_id>/confirm", methods=["POST"])
    def confirm_association_pdf(preview_id: int):
        if not valid_csrf():
            abort(400)
        db: Database = app.config["DATABASE"]
        preview = db.get_pdf_preview(preview_id)
        if not preview:
            abort(404)
        if not db.mark_pdf_preview_imported(preview_id):
            flash(translate(g.language, "flash_pdf_already_imported"), "error")
            return redirect(url_for("dashboard"))
        crawl_websites = request.form.get("crawl_websites") == "on"
        task_id = db.create_task(
            "association member", preview["association_name"], "association",
            f"pdf:{preview_id}", ASSOCIATION_IMPORT_LIMIT, crawl_websites,
        )
        EXECUTOR.submit(_run_task, db, task_id)
        flash(translate(g.language, "flash_pdf_queued", id=task_id), "success")
        return redirect(url_for("dashboard"))

    @app.route("/tasks/<int:task_id>/delete", methods=["POST"])
    def delete_task(task_id: int):
        if not valid_csrf():
            abort(400)
        deleted = app.config["DATABASE"].delete_task(task_id)
        key = "flash_task_deleted" if deleted else "flash_task_delete_blocked"
        flash(translate(g.language, key), "success" if deleted else "error")
        return redirect(url_for("dashboard"))

    @app.route("/api/tasks/<int:task_id>", methods=["GET"])
    def task_status(task_id: int):
        task = app.config["DATABASE"].get_task(task_id)
        if not task:
            return jsonify({"error": "not found"}), 404
        return jsonify(task)

    @app.route("/companies", methods=["GET"])
    def companies():
        query = request.args.get("q", "").strip()
        market = request.args.get("market", "").strip()
        if market not in BUSINESS_STATES:
            market = ""
        try:
            min_score = max(0, min(120, int(request.args.get("min_score", "0"))))
        except ValueError:
            min_score = 0
        db: Database = app.config["DATABASE"]
        return render_template(
            "companies.html",
            companies=db.list_companies(query=query, min_score=min_score, market=market),
            query=query,
            min_score=min_score,
            market=market,
            business_states=BUSINESS_STATES,
            stats=db.stats(),
        )

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        db: Database = app.config["DATABASE"]
        if request.method == "POST":
            if not valid_csrf():
                abort(400)
            if request.form.get("settings_section") == "scoring":
                values: dict[str, object]
                if request.form.get("reset_scoring") == "1":
                    values = dict(DEFAULT_SCORING_WEIGHTS)
                else:
                    values = {
                        key: request.form.get(f"score_{key}", "")
                        for key in DEFAULT_SCORING_WEIGHTS
                    }
                try:
                    updated = db.set_scoring_weights(values)
                except ValueError:
                    flash(translate(g.language, "flash_scoring_invalid"), "error")
                else:
                    key = "flash_scoring_reset" if request.form.get("reset_scoring") == "1" else "flash_scoring_saved"
                    flash(translate(g.language, key, count=updated), "success")
                return redirect(url_for("settings") + "#scoring-rules")
            if request.form.get("remove_brave_key") == "on":
                db.set_setting(BRAVE_API_SETTING, "")
                flash(translate(g.language, "flash_api_removed"), "success")
            else:
                api_key = request.form.get("brave_api_key", "").strip()[:500]
                if api_key:
                    db.set_setting(BRAVE_API_SETTING, api_key)
                    flash(translate(g.language, "flash_api_saved"), "success")
                else:
                    flash(translate(g.language, "flash_api_key_required"), "error")
            return redirect(url_for("settings"))

        configured_key = brave_api_key(db)
        return render_template(
            "settings.html",
            brave_configured=bool(configured_key),
            brave_masked=("••••" + configured_key[-4:]) if configured_key else "",
            brave_from_environment=bool(
                not db.get_setting(BRAVE_API_SETTING).strip()
                and os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
            ),
            scoring_weights=db.get_scoring_weights(),
            scoring_total=SCORING_TOTAL,
        )

    @app.route("/companies/new", methods=["GET", "POST"])
    def create_company():
        if request.method == "POST":
            if not valid_csrf():
                abort(400)
            try:
                company_id = app.config["DATABASE"].create_company(company_from_form())
            except ValueError as exc:
                key = company_error_key(exc)
                flash(translate(g.language, key), "error")
                return render_template("company_form.html", company=request.form, mode="create"), 400
            flash(translate(g.language, "flash_company_created"), "success")
            return redirect(url_for("edit_company", company_id=company_id))
        return render_template("company_form.html", company={}, mode="create")

    @app.route("/companies/import", methods=["GET", "POST"])
    def import_database():
        if request.method == "GET":
            return render_template("database_import.html")
        if not valid_csrf():
            abort(400)
        uploaded = request.files.get("database_file")
        if not uploaded or not uploaded.filename:
            flash(translate(g.language, "flash_choose_database_file"), "error")
            return redirect(url_for("import_database"))
        filename = Path(uploaded.filename.replace("\\", "/")).name
        if Path(filename).suffix.casefold() not in {".csv", ".xlsx", ".db", ".sqlite", ".sqlite3"}:
            flash(translate(g.language, "flash_invalid_database_file"), "error")
            return redirect(url_for("import_database"))
        payload = uploaded.read(MAX_DATABASE_IMPORT_BYTES + 1)
        if not payload or len(payload) > MAX_DATABASE_IMPORT_BYTES:
            flash(translate(g.language, "flash_database_file_size"), "error")
            return redirect(url_for("import_database"))
        try:
            leads = parse_database_file(filename, payload)
        except Exception as exc:
            LOG.warning("Original database import failed: %s", exc)
            flash(translate(g.language, "flash_invalid_database_file"), "error")
            return redirect(url_for("import_database"))
        if not leads:
            flash(translate(g.language, "flash_database_no_rows"), "error")
            return redirect(url_for("import_database"))

        db: Database = app.config["DATABASE"]
        scoring_weights = db.get_scoring_weights()
        annotated: list[dict[str, object]] = []
        seen_import_keys: set[str] = set()
        for lead in leads:
            prepare_output_fields(lead)
            if not lead.market:
                continue
            score_lead(lead, weights=scoring_weights)
            match, reason = db.find_duplicate_company(lead)
            import_key = domain_key(lead.website) or "name:" + " ".join(lead.name.casefold().split())
            if match:
                row_status = "duplicate"
                match_company_id = match["id"]
                duplicate_reason = reason
            elif import_key in seen_import_keys:
                row_status = "duplicate"
                match_company_id = None
                duplicate_reason = "file"
            else:
                row_status = "new"
                match_company_id = None
                duplicate_reason = ""
            seen_import_keys.add(import_key)
            annotated.append({
                "lead": lead,
                "row_status": row_status,
                "match_company_id": match_company_id,
                "duplicate_reason": duplicate_reason,
                "missing_fields": missing_fields(lead),
            })
        if not annotated:
            flash(translate(g.language, "flash_outside_target_market"), "error")
            return redirect(url_for("import_database"))
        batch_id = db.create_database_import_batch(filename, annotated)
        return render_template(
            "database_import_preview.html",
            batch=db.get_database_import_batch(batch_id),
            rows=db.list_database_import_rows(batch_id),
        )

    @app.route("/companies/import/<int:batch_id>/confirm", methods=["POST"])
    def confirm_database_import(batch_id: int):
        if not valid_csrf():
            abort(400)
        db: Database = app.config["DATABASE"]
        if not db.get_database_import_batch(batch_id):
            abort(404)
        try:
            created, merged = db.confirm_database_import(batch_id)
        except ValueError:
            flash(translate(g.language, "flash_database_already_imported"), "error")
            return redirect(url_for("companies"))
        flash(
            translate(g.language, "flash_database_imported", created=created, merged=merged),
            "success",
        )
        return redirect(url_for("companies"))

    @app.route("/companies/<int:company_id>/edit", methods=["GET", "POST"])
    def edit_company(company_id: int):
        db: Database = app.config["DATABASE"]
        company = db.get_company(company_id)
        if not company:
            abort(404)
        if request.method == "POST":
            if not valid_csrf():
                abort(400)
            try:
                db.update_company(company_id, company_from_form())
            except ValueError as exc:
                key = company_error_key(exc)
                flash(translate(g.language, key), "error")
                submitted = request.form.to_dict()
                submitted["id"] = company_id
                return render_template("company_form.html", company=submitted, mode="edit"), 400
            flash(translate(g.language, "flash_company_updated"), "success")
            return redirect(url_for("edit_company", company_id=company_id))
        return render_template("company_form.html", company=company, mode="edit")

    @app.route("/companies/<int:company_id>/delete", methods=["POST"])
    def delete_company(company_id: int):
        if not valid_csrf():
            abort(400)
        deleted = app.config["DATABASE"].delete_company(company_id)
        if not deleted:
            abort(404)
        flash(translate(g.language, "flash_company_deleted"), "success")
        return redirect(url_for("companies"))

    @app.route("/companies/bulk/delete", methods=["POST"])
    def bulk_delete_companies():
        if not valid_csrf():
            abort(400)
        ids = selected_company_ids()
        if not ids:
            flash(translate(g.language, "flash_choose_companies"), "error")
            return redirect(url_for("companies"))
        deleted = app.config["DATABASE"].delete_companies(ids)
        flash(translate(g.language, "flash_companies_deleted", count=deleted), "success")
        return redirect(url_for("companies"))

    @app.route("/companies/bulk/export", methods=["POST"])
    def bulk_export_companies():
        if not valid_csrf():
            abort(400)
        rows = app.config["DATABASE"].list_companies_by_ids(selected_company_ids())
        if not rows:
            flash(translate(g.language, "flash_choose_companies"), "error")
            return redirect(url_for("companies"))
        return company_csv_response(rows, "leadharbor-selected-companies.csv")

    @app.route("/export.csv", methods=["GET"])
    def export_companies():
        query = request.args.get("q", "").strip()
        market = request.args.get("market", "").strip()
        if market not in BUSINESS_STATES:
            market = ""
        try:
            min_score = max(0, min(120, int(request.args.get("min_score", "0"))))
        except ValueError:
            min_score = 0
        rows = app.config["DATABASE"].list_companies(
            query=query, min_score=min_score, market=market, limit=100000,
        )
        return company_csv_response(rows, "leadharbor-companies.csv")

    return app


def _task_sources(task: dict, db: Database) -> list[object]:
    selected: list[object] = []
    source = task["source"]
    if source in {"all", "osm", "keyword"}:
        selected.append(OpenStreetMapSource())
    key = brave_api_key(db)
    if source == "search" or (source in {"all", "keyword"} and key):
        if not key:
            raise RuntimeError("BRAVE_SEARCH_API_KEY 未设置")
        selected.append(BraveSearchSource(key))
    association = task.get("association", "")
    if association == "rca" and source in {"all", "association"}:
        selected.append(RcaDirectorySource())
    if association.startswith("csv:") and source in {"all", "association"}:
        imports_dir = (app_data_dir() / "imports").resolve()
        csv_path = Path(association.removeprefix("csv:")).resolve()
        try:
            csv_path.relative_to(imports_dir)
        except ValueError as exc:
            raise RuntimeError("协会 CSV 文件路径无效") from exc
        if not csv_path.is_file():
            raise RuntimeError("协会 CSV 文件不存在")
        selected.append(AssociationSource(csv_paths=[csv_path]))
    if association.startswith("pdf:") and source in {"all", "association"}:
        try:
            preview_id = int(association.removeprefix("pdf:"))
        except ValueError as exc:
            raise RuntimeError("协会 PDF 任务无效") from exc
        preview = db.get_pdf_preview(preview_id)
        if not preview:
            raise RuntimeError("协会 PDF 预览不存在")
        imports_dir = (app_data_dir() / "imports").resolve()
        pdf_path = Path(preview["pdf_path"]).resolve()
        try:
            pdf_path.relative_to(imports_dir)
        except ValueError as exc:
            raise RuntimeError("协会 PDF 文件路径无效") from exc
        if not pdf_path.is_file():
            raise RuntimeError("协会 PDF 文件不存在")
        selected.append(PdfAssociationSource(
            preview["association_name"], pdf_path, source_url=preview["source_url"]
        ))
    return selected


def _run_task(db: Database, task_id: int) -> None:
    task = db.get_task(task_id)
    if not task:
        return
    db.update_task(task_id, status="running", started_at=utc_now())
    try:
        output = app_data_dir() / "exports" / f"task-{task_id}.csv"
        pipeline = LeadPipeline(
            pages_per_site=4,
            request_delay=1.0,
            crawl_websites=bool(task["crawl_websites"]),
            sources=_task_sources(task, db),
            scoring_weights=db.get_scoring_weights(),
        )
        leads = pipeline.run(task["keyword"], task["location"], task["requested_limit"], output)
        db.save_leads(leads, task_id)
        db.update_task(
            task_id,
            status="completed",
            result_count=len(leads),
            output_path=str(output),
            finished_at=utc_now(),
        )
    except Exception as exc:
        LOG.exception("Task %s failed", task_id)
        db.update_task(
            task_id,
            status="failed",
            error_message=str(exc)[:1000],
            finished_at=utc_now(),
        )


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    app.run(host="127.0.0.1", port=8765, debug=False)
