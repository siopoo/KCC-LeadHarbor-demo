from __future__ import annotations

import logging
import json
from typing import Any

from leadharbor.database import Database, utc_now

from .dedup import match_company, match_contact
from .diff import compare_properties, fill_missing_patch
from .mapper import map_company_properties, map_contact_properties


LOG = logging.getLogger("leadharbor.hubspot")


class HubSpotSyncService:
    def __init__(self, db: Database, companies: Any, contacts: Any, associations: Any) -> None:
        self.db = db
        self.companies = companies
        self.contacts = contacts
        self.associations = associations

    @staticmethod
    def _differences(remote: dict[str, Any] | None, local: dict[str, str]) -> list[dict[str, str]]:
        properties = (remote or {}).get("properties", {})
        return [item.as_dict() for item in compare_properties(properties, local)]

    @staticmethod
    def _fresh_get(api: Any, record_id: str) -> dict[str, Any]:
        reader = getattr(api, "get_fresh", api.get)
        return reader(record_id)

    def _check_one(
        self, company: dict[str, Any], company_available: set[str], contact_available: set[str],
        industry_options: dict[str, str] | None,
    ) -> dict[str, Any]:
        company_candidates = self.companies.candidates(
            company, str(company.get("hubspot_company_id", "")),
        )
        company_match = match_company(
            company, company_candidates,
            linked_id=str(company.get("hubspot_company_id", "")),
            integration_id=str(company.get("id", "")),
        )
        company_record = company_match.record or {}
        company_values = map_company_properties(
            company, available=company_available, industry_options=industry_options,
        )
        raw_industry = str(company.get("industry") or company.get("company_type") or "").strip()
        company_notes: list[str] = []
        if raw_industry and "industry" not in company_values:
            company_notes.append(
                "Industry was skipped because it could not be verified as an accepted HubSpot option."
            )
        company_differences = self._differences(company_record, company_values)
        company_conflicts = any(item["action"] == "CONFLICT" for item in company_differences)
        company_fills = any(item["action"] == "FILL_MISSING" for item in company_differences)

        if company_match.kind == "NO_MATCH":
            status = "NEW"
        elif company_match.kind in {"POSSIBLE_MATCH", "AMBIGUOUS"} or company_conflicts:
            status = "CONFLICT"
        elif company_fills:
            status = "ENRICHABLE"
        else:
            status = "DUPLICATE"

        contact_values = map_contact_properties(company, available=contact_available)
        contact_match = None
        contact_record: dict[str, Any] = {}
        contact_differences: list[dict[str, str]] = []
        if contact_values:
            contact_candidates = self.contacts.candidates(
                company, str(company.get("hubspot_contact_id", "")),
            )
            contact_match = match_contact(
                company, contact_candidates,
                linked_id=str(company.get("hubspot_contact_id", "")),
                integration_id=str(company.get("id", "")),
            )
            contact_record = contact_match.record or {}
            contact_differences = self._differences(contact_record, contact_values)
            contact_conflicts = any(
                item["action"] == "CONFLICT" for item in contact_differences
            )
            contact_fills = any(
                item["action"] == "FILL_MISSING" for item in contact_differences
            )
            if contact_match.kind in {"POSSIBLE_MATCH", "AMBIGUOUS"} or contact_conflicts:
                status = "CONFLICT"
            elif status == "DUPLICATE" and (
                contact_match.kind == "NO_MATCH" or contact_fills
            ):
                status = "ENRICHABLE"

        match_reason = company_match.reason
        match_confidence = company_match.confidence
        return {
            "company_id": company["id"],
            "company_name": company["name"],
            "status": status,
            "match_reason": match_reason,
            "match_confidence": match_confidence,
            "contact_match_reason": contact_match.reason if contact_match else "",
            "hubspot_company_id": company_match.record_id,
            "hubspot_contact_id": contact_match.record_id if contact_match else "",
            "hubspot_company_updated_at": str(company_record.get("updatedAt", "")),
            "hubspot_contact_updated_at": str(contact_record.get("updatedAt", "")),
            "company_record": company_record,
            "contact_record": contact_record,
            "company_properties": company_values,
            "contact_properties": contact_values,
            "company_differences": company_differences,
            "contact_differences": contact_differences,
            "company_notes": company_notes,
            "error": "",
        }

    def check(self, company_ids: list[int]) -> dict[str, Any]:
        rows = self.db.list_companies_by_ids(company_ids)
        company_available = self.companies.available_properties()
        contact_available = self.contacts.available_properties()
        industry_options_reader = getattr(self.companies, "industry_options", None)
        industry_options = industry_options_reader() if industry_options_reader else None
        company_links = [str(row.get("hubspot_company_id", "")) for row in rows if row.get("hubspot_company_id")]
        contact_links = [str(row.get("hubspot_contact_id", "")) for row in rows if row.get("hubspot_contact_id")]
        if company_links and hasattr(self.companies, "preload"):
            self.companies.preload(company_links)
        if contact_links and hasattr(self.contacts, "preload"):
            self.contacts.preload(contact_links)
        results: list[dict[str, Any]] = []
        for company in rows:
            try:
                results.append(self._check_one(
                    company, company_available, contact_available, industry_options,
                ))
            except Exception as exc:
                LOG.warning("HubSpot check failed for local company %s: %s", company["id"], exc)
                results.append({
                    "company_id": company["id"], "company_name": company["name"],
                    "status": "FAILED", "match_reason": "", "match_confidence": "",
                    "hubspot_company_id": "", "hubspot_contact_id": "",
                    "hubspot_company_updated_at": "", "hubspot_contact_updated_at": "",
                    "company_record": {}, "contact_record": {},
                    "company_properties": {}, "contact_properties": {},
                    "company_differences": [], "contact_differences": [],
                    "company_notes": [],
                    "error": str(exc)[:500],
                })
        batch_id = self.db.create_hubspot_check_batch(results)
        summary = {
            key.casefold(): sum(result["status"] == key for result in results)
            for key in ("NEW", "DUPLICATE", "ENRICHABLE", "CONFLICT", "FAILED")
        }
        return {"batch_id": batch_id, "summary": summary, "results": results}

    @staticmethod
    def _safe_actions(value: object) -> set[str]:
        allowed = {
            "CREATE_COMPANY", "CREATE_CONTACT", "ENRICH_COMPANY",
            "ENRICH_CONTACT", "ASSOCIATE_CONTACT_COMPANY", "SKIP",
        }
        return {
            str(item) for item in value if str(item) in allowed
        } if isinstance(value, list) else set()

    @staticmethod
    def _safe_fields(value: object) -> set[str]:
        return {
            str(item) for item in value if isinstance(item, str) and item
        } if isinstance(value, list) else set()

    @staticmethod
    def _summary(results: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "success": sum(item["status"] == "SYNCED" for item in results),
            "failed": sum(item["status"] == "FAILED" for item in results),
            "skipped": sum(item["status"] == "SKIPPED" for item in results),
            "conflict": sum(
                item["status"] in {"CONFLICT", "RECHECK_REQUIRED"} for item in results
            ),
        }

    def sync(self, batch_id: int, approvals: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply only explicitly approved actions from a persisted read-only preview."""
        if not self.db.get_hubspot_check_batch(batch_id):
            raise ValueError("HubSpot check batch was not found")
        company_available = self.companies.available_properties()
        contact_available = self.contacts.available_properties()
        industry_options_reader = getattr(self.companies, "industry_options", None)
        industry_options = industry_options_reader() if industry_options_reader else None
        sync_timestamp = utc_now()
        plans: dict[str, dict[str, Any]] = {}
        immediate: list[dict[str, Any]] = []

        for approval in approvals:
            try:
                company_id = int(approval.get("company_id", 0))
            except (TypeError, ValueError):
                continue
            snapshot = self.db.get_hubspot_check_result(batch_id, company_id)
            local = self.db.get_company(company_id)
            if not snapshot or not local:
                immediate.append({
                    "company_id": company_id, "status": "FAILED",
                    "action": "", "error": "The checked LeadHarbor record was not found.",
                })
                continue
            if snapshot.get("sync_status") == "SYNCED":
                immediate.append({
                    "company_id": company_id, "status": "SYNCED",
                    "action": snapshot.get("sync_action", ""), "error": "",
                    "hubspot_company_id": local.get("hubspot_company_id", ""),
                    "hubspot_contact_id": local.get("hubspot_contact_id", ""),
                })
                continue
            actions = self._safe_actions(approval.get("actions"))
            if not actions or "SKIP" in actions:
                immediate.append({
                    "company_id": company_id, "status": "SKIPPED", "action": "SKIP", "error": "",
                })
                self.db.record_hubspot_sync_result(
                    company_id, status="SKIPPED", batch_id=batch_id, action="SKIP",
                )
                continue
            company_overrides = self._safe_fields(approval.get("company_overwrite_fields"))
            contact_overrides = self._safe_fields(approval.get("contact_overwrite_fields"))
            if snapshot["status"] == "FAILED":
                immediate.append({
                    "company_id": company_id, "status": "FAILED", "action": "",
                    "error": snapshot.get("error_message") or "The HubSpot check failed.",
                })
                continue
            if snapshot["status"] == "CONFLICT" and snapshot["match_confidence"] != "exact":
                immediate.append({
                    "company_id": company_id, "status": "CONFLICT", "action": "",
                    "error": "A possible duplicate must be reviewed before syncing.",
                })
                self.db.record_hubspot_sync_result(
                    company_id, status="CONFLICT", error="Possible duplicate requires review.",
                    batch_id=batch_id,
                )
                continue
            company_differences = json.loads(snapshot["company_differences_json"] or "[]")
            contact_differences = json.loads(snapshot["contact_differences_json"] or "[]")
            unresolved_company = {
                item["field"] for item in company_differences
                if item.get("action") == "CONFLICT" and item.get("field") not in company_overrides
            }
            unresolved_contact = {
                item["field"] for item in contact_differences
                if item.get("action") == "CONFLICT" and item.get("field") not in contact_overrides
            }
            if snapshot["status"] == "CONFLICT" and (unresolved_company or unresolved_contact):
                immediate.append({
                    "company_id": company_id, "status": "CONFLICT", "action": "",
                    "error": "Conflicting fields default to keeping HubSpot values.",
                })
                self.db.record_hubspot_sync_result(
                    company_id, status="CONFLICT", error="Conflicting fields were not approved.",
                    batch_id=batch_id,
                )
                continue
            key = str(company_id)
            plans[key] = {
                "key": key, "company_id": company_id, "local": local,
                "snapshot": snapshot, "actions": actions,
                "company_overrides": company_overrides,
                "contact_overrides": contact_overrides,
                "company_id_remote": str(local.get("hubspot_company_id") or snapshot["hubspot_company_id"] or ""),
                "contact_id_remote": str(local.get("hubspot_contact_id") or snapshot["hubspot_contact_id"] or ""),
                "company_properties": map_company_properties(
                    local, available=company_available, enriched_at=sync_timestamp,
                    industry_options=industry_options,
                ),
                "contact_properties": map_contact_properties(
                    local, available=contact_available, enriched_at=sync_timestamp,
                ),
                "performed": [], "error": "", "status": "",
            }

        company_creates: list[tuple[str, dict[str, str]]] = []
        company_updates: list[tuple[str, str, dict[str, str]]] = []
        for key, plan in plans.items():
            remote_id = plan["company_id_remote"]
            if remote_id and "ENRICH_COMPANY" in plan["actions"]:
                try:
                    current = self._fresh_get(self.companies, remote_id)
                except Exception as exc:
                    plan["error"] = str(exc)[:500]
                    continue
                preview_version = plan["snapshot"]["hubspot_company_updated_at"]
                if preview_version and str(current.get("updatedAt", "")) != preview_version:
                    plan["status"] = "RECHECK_REQUIRED"
                    plan["error"] = "The HubSpot company changed after preview. Check again."
                    continue
                differences = compare_properties(
                    current.get("properties", {}), plan["company_properties"],
                )
                patch = fill_missing_patch(differences)
                for field in plan["company_overrides"]:
                    value = plan["company_properties"].get(field, "")
                    if value:
                        patch[field] = value
                if patch:
                    company_updates.append((key, remote_id, patch))
            elif not remote_id and "CREATE_COMPANY" in plan["actions"]:
                if plan["company_properties"]:
                    company_creates.append((key, plan["company_properties"]))
                else:
                    plan["error"] = "No valid company fields are available to create."
            elif not remote_id:
                plan["status"] = "SKIPPED"

        if company_creates:
            for key, outcome in self.companies.create_many(company_creates).items():
                plan = plans[key]
                if outcome.get("error"):
                    plan["error"] = str(outcome["error"])[:500]
                else:
                    plan["company_id_remote"] = str(outcome["record"].get("id", ""))
                    plan["performed"].append("CREATE_COMPANY")
        if company_updates:
            for key, outcome in self.companies.update_many(company_updates).items():
                plan = plans[key]
                if outcome.get("error"):
                    plan["error"] = str(outcome["error"])[:500]
                else:
                    plan["performed"].append("ENRICH_COMPANY")

        contact_creates: list[tuple[str, dict[str, str]]] = []
        contact_updates: list[tuple[str, str, dict[str, str]]] = []
        for key, plan in plans.items():
            if plan["error"] or plan["status"] in {"RECHECK_REQUIRED", "SKIPPED"}:
                continue
            remote_id = plan["contact_id_remote"]
            if remote_id and "ENRICH_CONTACT" in plan["actions"]:
                try:
                    current = self._fresh_get(self.contacts, remote_id)
                except Exception as exc:
                    plan["error"] = str(exc)[:500]
                    continue
                preview_version = plan["snapshot"]["hubspot_contact_updated_at"]
                if preview_version and str(current.get("updatedAt", "")) != preview_version:
                    plan["status"] = "RECHECK_REQUIRED"
                    plan["error"] = "The HubSpot contact changed after preview. Check again."
                    continue
                differences = compare_properties(
                    current.get("properties", {}), plan["contact_properties"],
                )
                patch = fill_missing_patch(differences)
                for field in plan["contact_overrides"]:
                    value = plan["contact_properties"].get(field, "")
                    if value:
                        patch[field] = value
                if patch:
                    contact_updates.append((key, remote_id, patch))
            elif not remote_id and "CREATE_CONTACT" in plan["actions"]:
                if plan["contact_properties"]:
                    contact_creates.append((key, plan["contact_properties"]))

        if contact_creates:
            for key, outcome in self.contacts.create_many(contact_creates).items():
                plan = plans[key]
                if outcome.get("error"):
                    plan["error"] = str(outcome["error"])[:500]
                else:
                    plan["contact_id_remote"] = str(outcome["record"].get("id", ""))
                    plan["performed"].append("CREATE_CONTACT")
        if contact_updates:
            for key, outcome in self.contacts.update_many(contact_updates).items():
                plan = plans[key]
                if outcome.get("error"):
                    plan["error"] = str(outcome["error"])[:500]
                else:
                    plan["performed"].append("ENRICH_CONTACT")

        associations: list[tuple[str, str, str]] = []
        for key, plan in plans.items():
            if (
                not plan["error"]
                and plan["status"] not in {"RECHECK_REQUIRED", "SKIPPED"}
                and "ASSOCIATE_CONTACT_COMPANY" in plan["actions"]
                and plan["contact_id_remote"] and plan["company_id_remote"]
            ):
                associations.append((key, plan["contact_id_remote"], plan["company_id_remote"]))
        if associations:
            for key, outcome in self.associations.associate_many(associations).items():
                plan = plans[key]
                if outcome.get("error"):
                    plan["error"] = str(outcome["error"])[:500]
                else:
                    plan["performed"].append("ASSOCIATE_CONTACT_COMPANY")

        results = list(immediate)
        for plan in plans.values():
            if plan["status"] in {"RECHECK_REQUIRED", "SKIPPED"}:
                status = plan["status"]
            elif plan["error"]:
                status = "FAILED"
            else:
                status = "SYNCED"
            action = ",".join(plan["performed"] or sorted(plan["actions"]))
            self.db.record_hubspot_sync_result(
                plan["company_id"], status=status,
                hubspot_company_id=plan["company_id_remote"],
                hubspot_contact_id=plan["contact_id_remote"],
                error=plan["error"], batch_id=batch_id, action=action,
            )
            results.append({
                "company_id": plan["company_id"], "status": status, "action": action,
                "hubspot_company_id": plan["company_id_remote"],
                "hubspot_contact_id": plan["contact_id_remote"],
                "error": plan["error"],
            })
        results.sort(key=lambda item: int(item.get("company_id", 0)))
        return {"summary": self._summary(results), "results": results}
