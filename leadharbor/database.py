from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .classification import infer_company_type, infer_market, prepare_output_fields
from .models import Lead
from .net import domain_key
from .scoring import (
    DEFAULT_SCORING_WEIGHTS, SCORING_SETTING_KEY, SCORING_VERSION,
    score_lead, validate_scoring_weights,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def merge_evidence_values(existing: str, incoming: str, separator: str = " | ") -> str:
    """Combine delimited evidence without losing earlier discovery sources."""
    values: list[str] = []
    for group in (existing, incoming):
        for value in (group or "").split(separator):
            cleaned = value.strip()
            if cleaned and cleaned.casefold() not in {item.casefold() for item in values}:
                values.append(cleaned)
    return separator.join(values)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS crawl_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL,
                    location TEXT NOT NULL,
                    source TEXT NOT NULL,
                    association TEXT NOT NULL DEFAULT '',
                    requested_limit INTEGER NOT NULL,
                    crawl_websites INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'queued',
                    result_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT '',
                    output_path TEXT NOT NULL DEFAULT '',
                    progress INTEGER NOT NULL DEFAULT 0,
                    progress_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unique_key TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT '',
                    company_type TEXT NOT NULL DEFAULT '',
                    contact_first_name TEXT NOT NULL DEFAULT '',
                    contact_last_name TEXT NOT NULL DEFAULT '',
                    website TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    signal TEXT NOT NULL DEFAULT '',
                    scale TEXT NOT NULL DEFAULT '',
                    current_lead_status TEXT NOT NULL DEFAULT 'Unchecked',
                    score INTEGER NOT NULL DEFAULT 0,
                    matched_keywords TEXT NOT NULL DEFAULT '',
                    email_status TEXT NOT NULL DEFAULT 'unchecked',
                    phone_status TEXT NOT NULL DEFAULT 'unchecked',
                    contact_notes TEXT NOT NULL DEFAULT '',
                    task_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES crawl_tasks(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_companies_score ON companies(score DESC);
                CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);
                CREATE INDEX IF NOT EXISTS idx_tasks_created ON crawl_tasks(created_at DESC);

                CREATE TABLE IF NOT EXISTS association_pdf_previews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    association_name TEXT NOT NULL,
                    pdf_path TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    member_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'ready',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pdf_previews_created
                ON association_pdf_previews(created_at DESC);

                CREATE TABLE IF NOT EXISTS database_import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    new_count INTEGER NOT NULL DEFAULT 0,
                    duplicate_count INTEGER NOT NULL DEFAULT 0,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'ready',
                    created_at TEXT NOT NULL,
                    imported_at TEXT
                );
                CREATE TABLE IF NOT EXISTS database_import_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT '',
                    company_type TEXT NOT NULL DEFAULT '',
                    contact_first_name TEXT NOT NULL DEFAULT '',
                    contact_last_name TEXT NOT NULL DEFAULT '',
                    website TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    signal TEXT NOT NULL DEFAULT '',
                    scale TEXT NOT NULL DEFAULT '',
                    score INTEGER NOT NULL DEFAULT 0,
                    row_status TEXT NOT NULL,
                    match_company_id INTEGER,
                    duplicate_reason TEXT NOT NULL DEFAULT '',
                    missing_fields TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(batch_id) REFERENCES database_import_batches(id) ON DELETE CASCADE,
                    FOREIGN KEY(match_company_id) REFERENCES companies(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_database_import_rows_batch
                ON database_import_rows(batch_id, id);

                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            existing_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(companies)").fetchall()
            }
            migrations = {
                "market": "TEXT NOT NULL DEFAULT ''",
                "company_type": "TEXT NOT NULL DEFAULT ''",
                "contact_first_name": "TEXT NOT NULL DEFAULT ''",
                "contact_last_name": "TEXT NOT NULL DEFAULT ''",
                "signal": "TEXT NOT NULL DEFAULT ''",
                "scale": "TEXT NOT NULL DEFAULT ''",
                "current_lead_status": "TEXT NOT NULL DEFAULT 'Unchecked'",
                "email_status": "TEXT NOT NULL DEFAULT 'unchecked'",
                "phone_status": "TEXT NOT NULL DEFAULT 'unchecked'",
                "contact_notes": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in migrations.items():
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE companies ADD COLUMN {column} {definition}")
            task_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(crawl_tasks)").fetchall()
            }
            for column, definition in {
                "progress": "INTEGER NOT NULL DEFAULT 0",
                "progress_message": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if column not in task_columns:
                    conn.execute(f"ALTER TABLE crawl_tasks ADD COLUMN {column} {definition}")
            conn.execute("""
                UPDATE companies
                SET market = COALESCE(
                    (SELECT location FROM crawl_tasks WHERE crawl_tasks.id = companies.task_id),
                    ''
                )
                WHERE market = ''
            """)
            conn.execute("""
                DELETE FROM companies
                WHERE lower(trim(name)) = 'find a contractor'
                  AND website = '' AND email = '' AND phone = ''
            """)
            conn.execute("""
                DELETE FROM companies
                WHERE lower(name) LIKE 'find %contractor%'
                  AND (
                    lower(website) LIKE '%procore.com%'
                    OR lower(source_url) LIKE '%procore.com%'
                  )
            """)
            conn.execute("""
                UPDATE crawl_tasks
                SET status = 'failed',
                    error_message = CASE
                        WHEN error_message = '' THEN '协会名录没有导入任何成员；请检查网络或重新运行导入。'
                        ELSE error_message
                    END,
                    finished_at = COALESCE(finished_at, ?)
                WHERE source = 'association' AND status = 'completed' AND result_count = 0
            """, (utc_now(),))
            unclassified = conn.execute("""
                SELECT id, name, category, description
                FROM companies
                WHERE company_type = ''
            """).fetchall()
            for row in unclassified:
                lead = Lead(
                    name=row["name"],
                    category=row["category"],
                    description=row["description"],
                )
                conn.execute(
                    "UPDATE companies SET company_type = ? WHERE id = ?",
                    (infer_company_type(lead), row["id"]),
                )
            scoring_version = conn.execute(
                "SELECT value FROM app_metadata WHERE key = 'scoring_version'"
            ).fetchone()
            if not scoring_version or scoring_version["value"] != SCORING_VERSION:
                setting = conn.execute(
                    "SELECT value FROM app_metadata WHERE key = ?", (SCORING_SETTING_KEY,)
                ).fetchone()
                weights = self._decode_scoring_weights(setting["value"] if setting else "")
                rows = conn.execute("SELECT * FROM companies").fetchall()
                for row in rows:
                    lead = Lead(
                        name=row["name"], market=row["market"], company_type=row["company_type"],
                        contact_first_name=row["contact_first_name"],
                        contact_last_name=row["contact_last_name"], website=row["website"],
                        email=row["email"], phone=row["phone"], address=row["address"],
                        country=row["country"], category=row["category"],
                        description=row["description"], source=row["source"],
                        signal=row["signal"], scale=row["scale"],
                    )
                    score_lead(lead, weights=weights)
                    conn.execute(
                        "UPDATE companies SET score = ?, updated_at = ? WHERE id = ?",
                        (lead.score, utc_now(), row["id"]),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('scoring_version', ?)",
                    (SCORING_VERSION,),
                )
            conn.execute("PRAGMA optimize")

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_metadata WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            if value:
                conn.execute(
                    "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
                    (key, value),
                )
            else:
                conn.execute("DELETE FROM app_metadata WHERE key = ?", (key,))

    @staticmethod
    def _decode_scoring_weights(value: str) -> dict[str, int]:
        try:
            parsed = json.loads(value) if value else DEFAULT_SCORING_WEIGHTS
            return validate_scoring_weights(parsed)
        except (TypeError, ValueError, json.JSONDecodeError):
            return dict(DEFAULT_SCORING_WEIGHTS)

    def get_scoring_weights(self) -> dict[str, int]:
        return self._decode_scoring_weights(self.get_setting(SCORING_SETTING_KEY))

    def set_scoring_weights(self, values: dict[str, object]) -> int:
        weights = validate_scoring_weights(values)
        now = utc_now()
        updated = 0
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
                (SCORING_SETTING_KEY, json.dumps(weights, separators=(",", ":"))),
            )
            rows = conn.execute("SELECT * FROM companies").fetchall()
            for row in rows:
                lead = Lead(
                    name=row["name"], market=row["market"], company_type=row["company_type"],
                    contact_first_name=row["contact_first_name"],
                    contact_last_name=row["contact_last_name"], website=row["website"],
                    email=row["email"], phone=row["phone"], address=row["address"],
                    country=row["country"], category=row["category"],
                    description=row["description"], source=row["source"],
                    signal=row["signal"], scale=row["scale"],
                )
                score_lead(lead, weights=weights)
                conn.execute(
                    "UPDATE companies SET score = ?, updated_at = ? WHERE id = ?",
                    (lead.score, now, row["id"]),
                )
                updated += 1
        return updated

    def create_task(
        self, keyword: str, location: str, source: str, association: str,
        requested_limit: int, crawl_websites: bool,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT INTO crawl_tasks
                (keyword, location, source, association, requested_limit, crawl_websites, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (keyword, location, source, association, requested_limit, int(crawl_websites), utc_now()))
            return int(cursor.lastrowid)

    def recover_interrupted_tasks(self) -> int:
        """Close tasks whose in-memory workers disappeared during app shutdown."""
        finished_at = utc_now()
        with self.connect() as conn:
            cursor = conn.execute("""
                UPDATE crawl_tasks
                SET status = CASE WHEN status = 'cancelling' THEN 'cancelled' ELSE 'interrupted' END,
                    error_message = CASE
                        WHEN status != 'cancelling' AND error_message = '' THEN '程序在任务完成前关闭，请重新创建任务。'
                        ELSE error_message
                    END,
                    finished_at = ?
                WHERE status IN ('queued', 'running', 'cancelling')
            """, (finished_at,))
            return cursor.rowcount

    def update_task(self, task_id: int, **values: Any) -> None:
        allowed = {
            "status", "result_count", "error_message", "output_path", "started_at",
            "finished_at", "progress", "progress_message",
        }
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.connect() as conn:
            conn.execute(f"UPDATE crawl_tasks SET {assignments} WHERE id = ?", [*values.values(), task_id])

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM crawl_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM crawl_tasks ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def list_association_tasks(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT * FROM crawl_tasks
                WHERE source = 'association'
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(row) for row in rows]

    def delete_task(self, task_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("""
                DELETE FROM crawl_tasks
                WHERE id = ? AND status NOT IN ('queued', 'running', 'cancelling')
            """, (task_id,))
            return cursor.rowcount == 1

    def request_task_cancel(self, task_id: int) -> str:
        """Cancel a queued task immediately or mark a running task for cooperative cancellation."""
        with self.connect() as conn:
            task = conn.execute(
                "SELECT status FROM crawl_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task or task["status"] not in {"queued", "running", "cancelling"}:
                return ""
            if task["status"] == "queued":
                conn.execute("""
                    UPDATE crawl_tasks SET status = 'cancelled', progress_message = 'cancelled',
                        finished_at = ? WHERE id = ?
                """, (utc_now(), task_id))
                return "cancelled"
            conn.execute("""
                UPDATE crawl_tasks SET status = 'cancelling', progress_message = 'cancelling'
                WHERE id = ?
            """, (task_id,))
            return "cancelling"

    def retry_task(self, task_id: int) -> int | None:
        with self.connect() as conn:
            task = conn.execute(
                "SELECT * FROM crawl_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task or task["status"] not in {"failed", "interrupted", "cancelled"}:
                return None
        return self.create_task(
            task["keyword"], task["location"], task["source"], task["association"],
            task["requested_limit"], bool(task["crawl_websites"]),
        )

    def create_backup(self, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(str(target)) as destination:
            source.backup(destination)
        return target

    def restore_backup(self, source_path: Path) -> None:
        uri = f"file:{source_path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as source:
            tables = {
                row[0] for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if not {"companies", "crawl_tasks", "app_metadata"}.issubset(tables):
                raise ValueError("not a LeadHarbor backup")
            with self.connect() as destination:
                source.backup(destination)
        self.initialize()

    def create_pdf_preview(
        self, association_name: str, pdf_path: Path, source_url: str, member_count: int,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT INTO association_pdf_previews
                (association_name, pdf_path, source_url, member_count, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (association_name, str(pdf_path), source_url, member_count, utc_now()))
            return int(cursor.lastrowid)

    def get_pdf_preview(self, preview_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM association_pdf_previews WHERE id = ?", (preview_id,)
            ).fetchone()
        return dict(row) if row else None

    def mark_pdf_preview_imported(self, preview_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("""
                UPDATE association_pdf_previews SET status = 'imported'
                WHERE id = ? AND status = 'ready'
            """, (preview_id,))
            return cursor.rowcount == 1

    def find_duplicate_company(self, lead: Lead) -> tuple[dict[str, Any] | None, str]:
        website_key = domain_key(lead.website)
        normalized_name = " ".join(lead.name.casefold().split())
        with self.connect() as conn:
            if website_key:
                row = conn.execute(
                    "SELECT * FROM companies WHERE unique_key = ? LIMIT 1", (website_key,)
                ).fetchone()
                if row:
                    return dict(row), "website"
            row = conn.execute(
                "SELECT * FROM companies WHERE lower(trim(name)) = ? LIMIT 1",
                (normalized_name,),
            ).fetchone()
            if row:
                return dict(row), "name"
            if lead.email:
                row = conn.execute(
                    "SELECT * FROM companies WHERE lower(trim(email)) = ? LIMIT 1",
                    (lead.email.casefold().strip(),),
                ).fetchone()
                if row:
                    return dict(row), "email"
            if lead.phone:
                row = conn.execute(
                    "SELECT * FROM companies WHERE trim(phone) = ? LIMIT 1",
                    (lead.phone.strip(),),
                ).fetchone()
                if row:
                    return dict(row), "phone"
        return None, ""

    def create_database_import_batch(
        self, filename: str, rows: list[dict[str, Any]],
    ) -> int:
        target_rows: list[dict[str, Any]] = []
        for row in rows:
            prepare_output_fields(row["lead"])
            if row["lead"].market:
                target_rows.append(row)
        rows = target_rows
        new_count = sum(row["row_status"] == "new" for row in rows)
        duplicate_count = len(rows) - new_count
        missing_count = sum(bool(row["missing_fields"]) for row in rows)
        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT INTO database_import_batches
                (filename, total_count, new_count, duplicate_count, missing_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (filename, len(rows), new_count, duplicate_count, missing_count, utc_now()))
            batch_id = int(cursor.lastrowid)
            for row in rows:
                lead: Lead = row["lead"]
                conn.execute("""
                    INSERT INTO database_import_rows (
                        batch_id, name, market, company_type, contact_first_name,
                        contact_last_name, website, email, phone, address, signal,
                        scale, score, row_status, match_company_id, duplicate_reason,
                        missing_fields
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    batch_id, lead.name, lead.market, lead.company_type,
                    lead.contact_first_name, lead.contact_last_name, lead.website,
                    lead.email, lead.phone, lead.address, lead.signal, lead.scale,
                    lead.score, row["row_status"], row.get("match_company_id"),
                    row.get("duplicate_reason", ""), ",".join(row["missing_fields"]),
                ))
        return batch_id

    def get_database_import_batch(self, batch_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM database_import_batches WHERE id = ?", (batch_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_database_import_rows(self, batch_id: int, limit: int = 10_000) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT import_row.*, companies.name AS matched_company_name
                FROM database_import_rows AS import_row
                LEFT JOIN companies ON companies.id = import_row.match_company_id
                WHERE import_row.batch_id = ? ORDER BY import_row.id LIMIT ?
            """, (batch_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def merge_company_fill_blanks(self, company_id: int, lead: Lead) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("""
                UPDATE companies SET
                    market = CASE WHEN market = '' THEN ? ELSE market END,
                    company_type = CASE WHEN company_type = '' THEN ? ELSE company_type END,
                    contact_first_name = CASE WHEN contact_first_name = '' THEN ? ELSE contact_first_name END,
                    contact_last_name = CASE WHEN contact_last_name = '' THEN ? ELSE contact_last_name END,
                    website = CASE WHEN website = '' THEN ? ELSE website END,
                    email = CASE WHEN email = '' THEN ? ELSE email END,
                    phone = CASE WHEN phone = '' THEN ? ELSE phone END,
                    address = CASE WHEN address = '' THEN ? ELSE address END,
                    signal = CASE WHEN signal = '' THEN ? ELSE signal END,
                    scale = CASE WHEN scale = '' THEN ? ELSE scale END,
                    score = MAX(score, ?),
                    updated_at = ?
                WHERE id = ?
            """, (
                lead.market, lead.company_type, lead.contact_first_name,
                lead.contact_last_name, lead.website, lead.email, lead.phone,
                lead.address, lead.signal, lead.scale, lead.score, utc_now(), company_id,
            ))
            return cursor.rowcount == 1

    def confirm_database_import(self, batch_id: int) -> tuple[int, int]:
        batch = self.get_database_import_batch(batch_id)
        if not batch or batch["status"] != "ready":
            raise ValueError("import batch is not ready")
        created = 0
        merged = 0
        for row in self.list_database_import_rows(batch_id):
            lead = Lead(
                name=row["name"], market=row["market"], company_type=row["company_type"],
                contact_first_name=row["contact_first_name"],
                contact_last_name=row["contact_last_name"], website=row["website"],
                email=row["email"], phone=row["phone"], address=row["address"],
                signal=row["signal"], scale=row["scale"], score=row["score"],
                source=f"Original database import: {batch['filename']}",
            )
            match = self.get_company(row["match_company_id"]) if row["match_company_id"] else None
            if not match:
                match, _ = self.find_duplicate_company(lead)
            if match:
                self.merge_company_fill_blanks(match["id"], lead)
                merged += 1
                continue
            try:
                self.create_company(lead)
                created += 1
            except ValueError:
                match, _ = self.find_duplicate_company(lead)
                if match:
                    self.merge_company_fill_blanks(match["id"], lead)
                    merged += 1
        with self.connect() as conn:
            conn.execute("""
                UPDATE database_import_batches SET status = 'imported', imported_at = ?
                WHERE id = ? AND status = 'ready'
            """, (utc_now(), batch_id))
        return created, merged

    @staticmethod
    def _lead_key(lead: Lead) -> str:
        return domain_key(lead.website) or "name:" + " ".join(lead.name.casefold().split())

    def save_leads(self, leads: list[Lead], task_id: int) -> None:
        now = utc_now()
        with self.connect() as conn:
            for lead in leads:
                prepare_output_fields(lead)
                if not lead.market:
                    continue
                unique_key = self._lead_key(lead)
                name_key = "name:" + " ".join(lead.name.casefold().split())
                if unique_key != name_key:
                    name_only = conn.execute(
                        "SELECT id FROM companies WHERE unique_key = ?", (name_key,)
                    ).fetchone()
                    domain_row = conn.execute(
                        "SELECT id FROM companies WHERE unique_key = ?", (unique_key,)
                    ).fetchone()
                    if name_only and not domain_row:
                        conn.execute(
                            "UPDATE companies SET unique_key = ? WHERE id = ?",
                            (unique_key, name_only["id"]),
                        )
                existing_evidence = conn.execute(
                    "SELECT source, source_url, matched_keywords FROM companies WHERE unique_key = ?",
                    (unique_key,),
                ).fetchone()
                if existing_evidence:
                    lead.source = merge_evidence_values(
                        existing_evidence["source"], lead.source
                    )
                    lead.source_url = merge_evidence_values(
                        existing_evidence["source_url"], lead.source_url
                    )
                    lead.matched_keywords = merge_evidence_values(
                        existing_evidence["matched_keywords"], lead.matched_keywords, ", "
                    )
                conn.execute("""
                    INSERT INTO companies (
                        unique_key, name, market, company_type, contact_first_name, contact_last_name,
                        website, email, phone, address, country, category, description, source,
                        source_url, signal, scale, current_lead_status, score, matched_keywords, task_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(unique_key) DO UPDATE SET
                        name = excluded.name,
                        market = CASE WHEN excluded.market != '' THEN excluded.market ELSE companies.market END,
                        company_type = CASE WHEN excluded.company_type != '' THEN excluded.company_type ELSE companies.company_type END,
                        contact_first_name = CASE WHEN excluded.contact_first_name != '' THEN excluded.contact_first_name ELSE companies.contact_first_name END,
                        contact_last_name = CASE WHEN excluded.contact_last_name != '' THEN excluded.contact_last_name ELSE companies.contact_last_name END,
                        website = CASE WHEN excluded.website != '' THEN excluded.website ELSE companies.website END,
                        email = CASE WHEN excluded.email != '' THEN excluded.email ELSE companies.email END,
                        phone = CASE WHEN excluded.phone != '' THEN excluded.phone ELSE companies.phone END,
                        address = CASE WHEN excluded.address != '' THEN excluded.address ELSE companies.address END,
                        country = CASE WHEN excluded.country != '' THEN excluded.country ELSE companies.country END,
                        category = CASE WHEN excluded.category != '' THEN excluded.category ELSE companies.category END,
                        description = CASE WHEN excluded.description != '' THEN excluded.description ELSE companies.description END,
                        source = excluded.source,
                        source_url = excluded.source_url,
                        signal = CASE WHEN excluded.signal != '' THEN excluded.signal ELSE companies.signal END,
                        scale = CASE WHEN excluded.scale != '' THEN excluded.scale ELSE companies.scale END,
                        current_lead_status = excluded.current_lead_status,
                        score = MAX(companies.score, excluded.score),
                        matched_keywords = excluded.matched_keywords,
                        task_id = excluded.task_id,
                        updated_at = excluded.updated_at
                """, (
                    unique_key, lead.name, lead.market, lead.company_type,
                    lead.contact_first_name, lead.contact_last_name, lead.website, lead.email,
                    lead.phone, lead.address, lead.country, lead.category, lead.description,
                    lead.source, lead.source_url, lead.signal, lead.scale,
                    lead.current_lead_status, lead.score, lead.matched_keywords, task_id, now, now,
                ))

    def get_company(self, company_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        return dict(row) if row else None

    def create_company(self, lead: Lead) -> int:
        if not lead.name.strip():
            raise ValueError("company name is required")
        prepare_output_fields(lead)
        if not lead.market:
            raise ValueError("outside target market")
        if not lead.company_type:
            lead.company_type = infer_company_type(lead)
        now = utc_now()
        unique_key = self._lead_key(lead)
        try:
            with self.connect() as conn:
                cursor = conn.execute("""
                    INSERT INTO companies (
                        unique_key, name, market, company_type, contact_first_name, contact_last_name,
                        website, email, phone, address, country, category, description, source,
                        source_url, signal, scale, current_lead_status, score, matched_keywords, task_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """, (
                    unique_key, lead.name, lead.market, lead.company_type,
                    lead.contact_first_name, lead.contact_last_name, lead.website, lead.email,
                    lead.phone, lead.address, lead.country, lead.category, lead.description,
                    lead.source or "Manual", lead.source_url, lead.signal, lead.scale,
                    lead.current_lead_status, lead.score, lead.matched_keywords, now, now,
                ))
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("company already exists") from exc

    def update_company(self, company_id: int, lead: Lead) -> bool:
        if not lead.name.strip():
            raise ValueError("company name is required")
        prepare_output_fields(lead)
        if not lead.market:
            raise ValueError("outside target market")
        if not lead.company_type:
            lead.company_type = infer_company_type(lead)
        try:
            with self.connect() as conn:
                cursor = conn.execute("""
                    UPDATE companies SET
                        unique_key = ?, name = ?, market = ?, company_type = ?,
                        contact_first_name = ?, contact_last_name = ?, website = ?, email = ?,
                        phone = ?, address = ?, signal = ?, scale = ?, score = ?, updated_at = ?
                    WHERE id = ?
                """, (
                    self._lead_key(lead), lead.name, lead.market, lead.company_type,
                    lead.contact_first_name, lead.contact_last_name, lead.website, lead.email,
                    lead.phone, lead.address, lead.signal, lead.scale, lead.score, utc_now(), company_id,
                ))
                return cursor.rowcount == 1
        except sqlite3.IntegrityError as exc:
            raise ValueError("company already exists") from exc

    def delete_company(self, company_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
            return cursor.rowcount == 1

    def update_contact_status(
        self, company_id: int, email_status: str, phone_status: str, notes: str = "",
    ) -> bool:
        allowed = {"unchecked", "valid", "invalid"}
        email_status = email_status if email_status in allowed else "unchecked"
        phone_status = phone_status if phone_status in allowed else "unchecked"
        with self.connect() as conn:
            cursor = conn.execute("""
                UPDATE companies SET email_status = ?, phone_status = ?, contact_notes = ?,
                    updated_at = ? WHERE id = ?
            """, (email_status, phone_status, notes.strip()[:1000], utc_now(), company_id))
            return cursor.rowcount == 1

    @staticmethod
    def _duplicate_tokens(company: dict[str, Any]) -> dict[str, str]:
        email = (company.get("email") or "").split(",", 1)[0].strip().casefold()
        phone = "".join(character for character in (company.get("phone") or "") if character.isdigit())
        return {
            "website": domain_key(company.get("website") or ""),
            "name": " ".join((company.get("name") or "").casefold().split()),
            "email": email,
            "phone": phone if len(phone) >= 7 else "",
        }

    def find_duplicate_groups(self) -> list[dict[str, Any]]:
        """Return connected groups that share a website, name, email, or phone."""
        with self.connect() as conn:
            companies = [dict(row) for row in conn.execute(
                "SELECT * FROM companies ORDER BY score DESC, id"
            ).fetchall()]
        parent = {company["id"]: company["id"] for company in companies}
        reasons: dict[tuple[int, int], set[str]] = {}

        def find(value: int) -> int:
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        seen: dict[tuple[str, str], int] = {}
        for company in companies:
            for field, value in self._duplicate_tokens(company).items():
                if not value:
                    continue
                key = (field, value)
                other = seen.get(key)
                if other is None:
                    seen[key] = company["id"]
                    continue
                pair = tuple(sorted((other, company["id"])))
                reasons.setdefault(pair, set()).add(field)
                union(other, company["id"])

        grouped: dict[int, list[dict[str, Any]]] = {}
        for company in companies:
            grouped.setdefault(find(company["id"]), []).append(company)
        result: list[dict[str, Any]] = []
        for members in grouped.values():
            if len(members) < 2:
                continue
            member_ids = {member["id"] for member in members}
            group_reasons = sorted({
                reason for pair, pair_reasons in reasons.items()
                if set(pair).issubset(member_ids) for reason in pair_reasons
            })
            result.append({"companies": members, "reasons": group_reasons})
        return result

    def merge_companies(self, keep_id: int, remove_id: int) -> bool:
        if keep_id == remove_id:
            return False
        with self.connect() as conn:
            keep_row = conn.execute("SELECT * FROM companies WHERE id = ?", (keep_id,)).fetchone()
            remove_row = conn.execute("SELECT * FROM companies WHERE id = ?", (remove_id,)).fetchone()
            if not keep_row or not remove_row:
                return False
            keep, remove = dict(keep_row), dict(remove_row)
            fill_fields = (
                "market", "company_type", "contact_first_name", "contact_last_name", "website",
                "address", "country", "category", "description", "signal", "scale", "task_id",
            )
            merged = {
                field: keep.get(field) or remove.get(field) or "" for field in fill_fields
            }
            merged["task_id"] = keep.get("task_id") or remove.get("task_id") or None
            for field, separator in (
                ("email", ", "), ("phone", ", "), ("source", " | "),
                ("source_url", " | "), ("matched_keywords", ", "),
            ):
                merged[field] = merge_evidence_values(
                    keep.get(field, ""), remove.get(field, ""), separator,
                )
            for field in ("email_status", "phone_status"):
                statuses = {keep.get(field), remove.get(field)}
                merged[field] = "invalid" if "invalid" in statuses else (
                    "valid" if "valid" in statuses else "unchecked"
                )
            merged["contact_notes"] = merge_evidence_values(
                keep.get("contact_notes", ""), remove.get("contact_notes", ""), " | "
            )
            merged["current_lead_status"] = (
                keep.get("current_lead_status")
                if keep.get("current_lead_status") not in {"", "Unchecked"}
                else remove.get("current_lead_status") or "Unchecked"
            )
            merged["score"] = max(int(keep.get("score", 0)), int(remove.get("score", 0)))
            assignments = ", ".join(f"{field} = ?" for field in merged)
            conn.execute(
                f"UPDATE companies SET {assignments}, updated_at = ? WHERE id = ?",
                [*merged.values(), utc_now(), keep_id],
            )
            conn.execute(
                "UPDATE database_import_rows SET match_company_id = ? WHERE match_company_id = ?",
                (keep_id, remove_id),
            )
            conn.execute("DELETE FROM companies WHERE id = ?", (remove_id,))
            return True

    def delete_companies(self, company_ids: list[int]) -> int:
        ids = sorted({value for value in company_ids if value > 0})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            cursor = conn.execute(f"DELETE FROM companies WHERE id IN ({placeholders})", ids)
            return cursor.rowcount

    @staticmethod
    def _with_target_markets(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            lead = Lead(
                name=item["name"], market=item["market"], address=item["address"],
                country=item["country"],
            )
            assigned_market = infer_market(lead)
            if not assigned_market:
                continue
            if item["market"].strip().casefold() != assigned_market.casefold() and not item["address"]:
                item["address"] = item["market"]
            item["market"] = assigned_market
            results.append(item)
        return results

    def list_companies_by_ids(self, company_ids: list[int]) -> list[dict[str, Any]]:
        ids = sorted({value for value in company_ids if value > 0})
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM companies WHERE id IN ({placeholders}) ORDER BY score DESC, id DESC",
                ids,
            ).fetchall()
        return self._with_target_markets(rows)

    def list_companies(
        self, query: str = "", min_score: int = 0, market: str = "", limit: int = 250,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM companies WHERE score >= ?"
        params: list[Any] = [min_score]
        if query:
            sql += " AND (name LIKE ? OR market LIKE ? OR company_type LIKE ? OR category LIKE ? OR address LIKE ? OR description LIKE ? OR signal LIKE ?)"
            term = f"%{query}%"
            params.extend([term, term, term, term, term, term, term])
        sql += " ORDER BY score DESC, id DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        results = self._with_target_markets(rows)
        if market:
            results = [item for item in results if item["market"] == market]
        return results[:limit]

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM companies").fetchall()
            running = conn.execute(
                "SELECT COUNT(*) FROM crawl_tasks WHERE status IN ('queued', 'running', 'cancelling')"
            ).fetchone()[0]
        companies = self._with_target_markets(rows)
        total = len(companies)
        contactable = sum(bool(item["email"] or item["phone"]) for item in companies)
        websites = sum(bool(item["website"]) for item in companies)
        return {"total": total, "contactable": contactable, "websites": websites, "running": running}
