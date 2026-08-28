from __future__ import annotations

import csv
import hmac
import io
import logging
import os
import secrets
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for

from leadharbor.associations import (
    ASSOCIATION_PRESETS, AssociationSource, PdfAssociationSource, RcaDirectorySource,
)
from leadharbor.classification import TARGET_STATE_NAMES, prepare_output_fields
from leadharbor.database import Database, utc_now
from leadharbor.database_import import missing_fields, parse_database_file
from leadharbor.i18n import DEFAULT_LANGUAGE, LANGUAGES, translate
from leadharbor.models import Lead
from leadharbor.net import domain_key, normalize_url
from leadharbor.pipeline import LeadPipeline, TaskCancelled
from leadharbor.search import BraveSearchSource
from leadharbor.scoring import DEFAULT_SCORING_WEIGHTS, SCORING_TOTAL, score_details, score_lead
from leadharbor.sources import OpenStreetMapSource
from leadharbor.storage import app_data_dir, resource_path

LOG = logging.getLogger(__name__)
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="leadharbor-crawler")
BUSINESS_STATES = TARGET_STATE_NAMES
ASSOCIATION_IMPORT_LIMIT = 10_000
MAX_ASSOCIATION_CSV_BYTES = 5 * 1024 * 1024
MAX_ASSOCIATION_PDF_BYTES = 20 * 1024 * 1024
MAX_DATABASE_IMPORT_BYTES = 10 * 1024 * 1024
MAX_BACKUP_BYTES = 100 * 1024 * 1024
ASSOCIATION_LABELS = {"rca": "Retail Contractors Association (RCA)"}
BRAVE_API_SETTING = "brave_search_api_key"
TASK_CANCEL_EVENTS: dict[int, threading.Event] = {}
TASK_CANCEL_LOCK = threading.Lock()


def submit_task(db: Database, task_id: int) -> None:
    event = threading.Event()
    with TASK_CANCEL_LOCK:
        TASK_CANCEL_EVENTS[task_id] = event
    EXECUTOR.submit(_run_task, db, task_id, event)


def brave_api_key(db: Database) -> str:
    return db.get_setting(BRAVE_API_SETTING).strip() or os.getenv("BRAVE_SEARCH_API_KEY", "").strip()


def company_with_evidence(
    company: dict[str, object], weights: dict[str, int],
) -> dict[str, object]:
    """Add display-only score details and safe discovery links to a company row."""
    item = dict(company)
    lead = Lead(
        name=str(item.get("name", "")), market=str(item.get("market", "")),
        company_type=str(item.get("company_type", "")),
        contact_first_name=str(item.get("contact_first_name", "")),
        contact_last_name=str(item.get("contact_last_name", "")),
        website=str(item.get("website", "")), email=str(item.get("email", "")),
        phone=str(item.get("phone", "")), address=str(item.get("address", "")),
        country=str(item.get("country", "")), category=str(item.get("category", "")),
        description=str(item.get("description", "")), source=str(item.get("source", "")),
        source_url=str(item.get("source_url", "")), signal=str(item.get("signal", "")),
        scale=str(item.get("scale", "")),
    )
    details = score_details(lead, weights)
    item["score_details"] = [
        {
            "key": key,
            **detail,
            "percent": min(
                100,
                round(int(detail["points"]) * 100 / int(detail["max_points"]))
                if int(detail["max_points"]) else 0,
            ),
        }
        for key, detail in details.items()
    ]
    automatic_score = sum(int(detail["points"]) for detail in details.values())
    item["automatic_score"] = automatic_score
    item["score_differs"] = int(item.get("score", 0)) != automatic_score

    source_names: list[str] = []
    for value in str(item.get("source", "")).split(" | "):
        cleaned = value.strip()
        if cleaned and cleaned not in source_names:
            source_names.append(cleaned)
    item["source_names"] = source_names

    source_links: list[dict[str, str]] = []
    candidates = [*str(item.get("source_url", "")).split(" | ")]
    if item.get("website"):
        candidates.append(str(item["website"]))
    for candidate in candidates:
        url = normalize_url(candidate)
        if not url or any(link["url"] == url for link in source_links):
            continue
        host = (urlparse(url).hostname or url).removeprefix("www.")
        source_links.append({"url": url, "label": host})
    item["source_links"] = source_links
    return item


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

        def task_progress_label(task: dict) -> str:
            message = task.get("progress_message", "") or ""
            if message.startswith("processing:"):
                _, current, total = (message.split(":", 2) + ["", ""])[:3]
                return t("task_progress_processing", current=current, total=total)
            known = {"discovering", "exporting", "saving", "cancelling", "cancelled"}
            return t(f"task_progress_{message}") if message in known else ""

        def contact_status_label(status: str) -> str:
            return t(f"contact_status_{status if status in {'unchecked', 'valid', 'invalid'} else 'unchecked'}")

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
            "task_progress_label": task_progress_label,
            "contact_status_label": contact_status_label,
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
            "Score Breakdown", "Source", "Source URL", "Matched Keywords", "Updated At",
            "Email Status", "Phone Status", "Contact Notes",
        ]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        weights = app.config["DATABASE"].get_scoring_weights()
        for row in rows:
            enriched = company_with_evidence(row, weights)
            breakdown = "; ".join(
                f"{detail['key']}: {detail['points']}/{detail['max_points']}"
                for detail in enriched["score_details"]
            )
            writer.writerow({
                "Company": row["name"], "Market": row["market"], "Address": row["address"],
                "Type": row["company_type"], "Contact First Name": row["contact_first_name"],
                "Contact Last Name": row["contact_last_name"], "Contact Info": row["email"],
                "Phone Number (if available)": row["phone"], "Signal": row["signal"],
                "Scale": row["scale"], "Score": row["score"], "Score Breakdown": breakdown,
                "Source": row.get("source", ""), "Source URL": row.get("source_url", ""),
                "Matched Keywords": row.get("matched_keywords", ""),
                "Updated At": row.get("updated_at", ""),
                "Email Status": row.get("email_status", "unchecked"),
                "Phone Status": row.get("phone_status", "unchecked"),
                "Contact Notes": row.get("contact_notes", ""),
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
        submit_task(db, task_id)
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
        submit_task(db, task_id)
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
        submit_task(db, task_id)
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
        submit_task(db, task_id)
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

    @app.route("/tasks/<int:task_id>/cancel", methods=["POST"])
    def cancel_task(task_id: int):
        if not valid_csrf():
            abort(400)
        db: Database = app.config["DATABASE"]
        status = db.request_task_cancel(task_id)
        if not status:
            flash(translate(g.language, "flash_task_cancel_blocked"), "error")
        else:
            with TASK_CANCEL_LOCK:
                event = TASK_CANCEL_EVENTS.get(task_id)
            if event:
                event.set()
            flash(translate(g.language, "flash_task_cancelled"), "success")
        return redirect(url_for("dashboard"))

    @app.route("/tasks/<int:task_id>/retry", methods=["POST"])
    def retry_task(task_id: int):
        if not valid_csrf():
            abort(400)
        db: Database = app.config["DATABASE"]
        new_task_id = db.retry_task(task_id)
        if not new_task_id:
            flash(translate(g.language, "flash_task_retry_blocked"), "error")
        else:
            submit_task(db, new_task_id)
            flash(translate(g.language, "flash_task_retried", id=new_task_id), "success")
        return redirect(url_for("dashboard"))

    @app.route("/api/tasks/<int:task_id>", methods=["GET"])
    def task_status(task_id: int):
        task = app.config["DATABASE"].get_task(task_id)
        if not task:
            return jsonify({"error": "not found"}), 404
        message = task.get("progress_message", "") or ""
        if message.startswith("processing:"):
            _, current, total = (message.split(":", 2) + ["", ""])[:3]
            task["progress_label"] = translate(
                g.language, "task_progress_processing", current=current, total=total,
            )
        else:
            task["progress_label"] = translate(
                g.language, f"task_progress_{message}",
            ) if message in {"discovering", "exporting", "saving", "cancelling", "cancelled"} else ""
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
        rows = db.list_companies(query=query, min_score=min_score, market=market)
        weights = db.get_scoring_weights()
        return render_template(
            "companies.html",
            companies=[company_with_evidence(company, weights) for company in rows],
            query=query,
            min_score=min_score,
            market=market,
            business_states=BUSINESS_STATES,
            stats=db.stats(),
        )

    @app.route("/companies/duplicates", methods=["GET"])
    def duplicate_companies():
        groups = app.config["DATABASE"].find_duplicate_groups()
        return render_template("company_duplicates.html", groups=groups)

    @app.route("/companies/duplicates/merge", methods=["POST"])
    def merge_duplicate_companies():
        if not valid_csrf():
            abort(400)
        try:
            keep_id = int(request.form.get("keep_id", "0"))
            remove_id = int(request.form.get("remove_id", "0"))
        except ValueError:
            keep_id = remove_id = 0
        merged = app.config["DATABASE"].merge_companies(keep_id, remove_id)
        flash(
            translate(g.language, "flash_companies_merged" if merged else "flash_merge_failed"),
            "success" if merged else "error",
        )
        return redirect(url_for("duplicate_companies"))

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
        backups_dir = db.path.parent / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        backups = [
            {"name": path.name, "size": path.stat().st_size, "updated_at": path.stat().st_mtime}
            for path in sorted(backups_dir.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True)[:12]
        ]
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
            backups=backups,
        )

    @app.route("/settings/database/backup", methods=["POST"])
    def create_database_backup():
        if not valid_csrf():
            abort(400)
        db: Database = app.config["DATABASE"]
        timestamp = utc_now()[:19].replace("-", "").replace(":", "").replace("T", "-")
        filename = f"leadharbor-backup-{timestamp}-{secrets.token_hex(2)}.db"
        db.create_backup(db.path.parent / "backups" / filename)
        flash(translate(g.language, "flash_backup_created"), "success")
        return redirect(url_for("settings") + "#database-safety")

    @app.route("/settings/database/backup/<path:filename>", methods=["GET"])
    def download_database_backup(filename: str):
        if Path(filename).name != filename or not filename.endswith(".db"):
            abort(404)
        backup = app.config["DATABASE"].path.parent / "backups" / filename
        if not backup.is_file():
            abort(404)
        return send_file(backup, as_attachment=True, download_name=filename)

    @app.route("/settings/database/restore", methods=["POST"])
    def restore_database_backup():
        if not valid_csrf():
            abort(400)
        db: Database = app.config["DATABASE"]
        if db.stats()["running"]:
            flash(translate(g.language, "flash_restore_tasks_running"), "error")
            return redirect(url_for("settings") + "#database-safety")
        uploaded = request.files.get("backup_file")
        if not uploaded or not uploaded.filename:
            flash(translate(g.language, "flash_choose_backup"), "error")
            return redirect(url_for("settings") + "#database-safety")
        filename = Path(uploaded.filename.replace("\\", "/")).name
        payload = uploaded.read(MAX_BACKUP_BYTES + 1)
        if Path(filename).suffix.casefold() not in {".db", ".sqlite", ".sqlite3"} or not payload or len(payload) > MAX_BACKUP_BYTES:
            flash(translate(g.language, "flash_invalid_backup"), "error")
            return redirect(url_for("settings") + "#database-safety")
        imports_dir = db.path.parent / "imports"
        imports_dir.mkdir(parents=True, exist_ok=True)
        temporary = imports_dir / f"restore-{secrets.token_hex(8)}.db"
        temporary.write_bytes(payload)
        try:
            timestamp = utc_now()[:19].replace("-", "").replace(":", "").replace("T", "-")
            db.create_backup(db.path.parent / "backups" / f"before-restore-{timestamp}.db")
            db.restore_backup(temporary)
            db.recover_interrupted_tasks()
        except (ValueError, sqlite3.DatabaseError, OSError) as exc:
            LOG.warning("Database restore failed: %s", exc)
            flash(translate(g.language, "flash_invalid_backup"), "error")
        else:
            flash(translate(g.language, "flash_backup_restored"), "success")
        finally:
            temporary.unlink(missing_ok=True)
        return redirect(url_for("settings") + "#database-safety")

    @app.route("/companies/new", methods=["GET", "POST"])
    def create_company():
        if request.method == "POST":
            if not valid_csrf():
                abort(400)
            try:
                db: Database = app.config["DATABASE"]
                company_id = db.create_company(company_from_form())
                db.update_contact_status(
                    company_id, request.form.get("email_status", "unchecked"),
                    request.form.get("phone_status", "unchecked"),
                    request.form.get("contact_notes", ""),
                )
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
                db.update_contact_status(
                    company_id, request.form.get("email_status", "unchecked"),
                    request.form.get("phone_status", "unchecked"),
                    request.form.get("contact_notes", ""),
                )
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


def _run_task(db: Database, task_id: int, cancel_event: threading.Event | None = None) -> None:
    cancel_event = cancel_event or threading.Event()
    task = db.get_task(task_id)
    if not task:
        return
    if task["status"] in {"cancelled", "cancelling"} or cancel_event.is_set():
        db.update_task(
            task_id, status="cancelled", progress_message="cancelled", finished_at=utc_now(),
        )
        with TASK_CANCEL_LOCK:
            TASK_CANCEL_EVENTS.pop(task_id, None)
        return
    db.update_task(
        task_id, status="running", progress=1, progress_message="discovering",
        started_at=utc_now(),
    )
    try:
        output = app_data_dir() / "exports" / f"task-{task_id}.csv"
        pipeline = LeadPipeline(
            pages_per_site=4,
            request_delay=1.0,
            crawl_websites=bool(task["crawl_websites"]),
            sources=_task_sources(task, db),
            scoring_weights=db.get_scoring_weights(),
        )
        leads = pipeline.run(
            task["keyword"], task["location"], task["requested_limit"], output,
            progress=lambda value, message: db.update_task(
                task_id, progress=max(0, min(99, value)), progress_message=message,
            ),
            is_cancelled=cancel_event.is_set,
        )
        db.save_leads(leads, task_id)
        db.update_task(
            task_id,
            status="completed",
            result_count=len(leads),
            output_path=str(output),
            progress=100,
            progress_message="",
            finished_at=utc_now(),
        )
    except TaskCancelled:
        db.update_task(
            task_id, status="cancelled", progress_message="cancelled", finished_at=utc_now(),
        )
    except Exception as exc:
        LOG.exception("Task %s failed", task_id)
        db.update_task(
            task_id,
            status="failed",
            error_message=str(exc)[:1000],
            finished_at=utc_now(),
        )
    finally:
        with TASK_CANCEL_LOCK:
            TASK_CANCEL_EVENTS.pop(task_id, None)


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    app.run(host="127.0.0.1", port=8765, debug=False)
