from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .models import MatchDecision
from .normalization import (
    normalize_company_name,
    normalize_domain,
    normalize_email,
    normalize_phone,
)


def _properties(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("properties", {})
    return value if isinstance(value, Mapping) else {}


def _decision_for_exact(records: list[dict[str, Any]], reason: str) -> MatchDecision:
    if len(records) == 1:
        record = records[0]
        return MatchDecision(
            "EXACT_MATCH", str(record.get("id", "")), reason, "exact", record,
            tuple(records),
        )
    if len(records) > 1:
        return MatchDecision("AMBIGUOUS", reason=reason, confidence="conflict", candidates=tuple(records))
    return MatchDecision("NO_MATCH")


def _same_phone(left: str, right: str) -> bool:
    normalized_left = normalize_phone(left)
    normalized_right = normalize_phone(right)
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    # HubSpot may store North American numbers with or without country code.
    return (
        len(normalized_left) in {10, 11} and len(normalized_right) in {10, 11}
        and normalized_left[-10:] == normalized_right[-10:]
    )


def match_company(
    local: Mapping[str, Any], candidates: Sequence[dict[str, Any]],
    linked_id: str = "", integration_id: str = "",
) -> MatchDecision:
    records = list(candidates)
    if linked_id:
        linked = [record for record in records if str(record.get("id", "")) == linked_id]
        if linked:
            return _decision_for_exact(linked, "local_hubspot_id")
    if integration_id:
        integrated = [
            record for record in records
            if str(_properties(record).get("leadharbor_company_id", "")) == integration_id
        ]
        if integrated:
            return _decision_for_exact(integrated, "leadharbor_company_id")
    domain = normalize_domain(str(local.get("website") or local.get("domain") or ""))
    if domain:
        exact = [
            record for record in records
            if normalize_domain(str(_properties(record).get("domain", ""))) == domain
        ]
        if exact:
            return _decision_for_exact(exact, "domain")
    name = normalize_company_name(str(local.get("name", "")))
    state = str(local.get("state") or local.get("market") or "").strip().casefold()
    possible = [
        record for record in records
        if name and normalize_company_name(str(_properties(record).get("name", ""))) == name
    ]
    if possible:
        same_state = [
            record for record in possible
            if state and str(_properties(record).get("state", "")).strip().casefold() == state
        ]
        choices = same_state or possible
        if len(choices) == 1:
            record = choices[0]
            return MatchDecision(
                "POSSIBLE_MATCH", str(record.get("id", "")),
                "name_state" if same_state else "name", "possible", record, tuple(choices),
            )
        return MatchDecision("AMBIGUOUS", reason="name", confidence="conflict", candidates=tuple(choices))
    return MatchDecision("NO_MATCH")


def match_contact(
    local: Mapping[str, Any], candidates: Sequence[dict[str, Any]],
    linked_id: str = "", integration_id: str = "",
) -> MatchDecision:
    records = list(candidates)
    if linked_id:
        linked = [record for record in records if str(record.get("id", "")) == linked_id]
        if linked:
            return _decision_for_exact(linked, "local_hubspot_id")
    if integration_id:
        integrated = [
            record for record in records
            if str(_properties(record).get("leadharbor_contact_id", "")) == integration_id
        ]
        if integrated:
            return _decision_for_exact(integrated, "leadharbor_contact_id")
    email = normalize_email(str(local.get("email", "")))
    if email:
        exact = [
            record for record in records
            if normalize_email(str(_properties(record).get("email", ""))) == email
        ]
        if exact:
            return _decision_for_exact(exact, "email")
    phone = normalize_phone(str(local.get("contact_phone", "")))
    first = str(local.get("contact_first_name") or local.get("firstname") or "").strip().casefold()
    last = str(local.get("contact_last_name") or local.get("lastname") or "").strip().casefold()
    possible: list[dict[str, Any]] = []
    reason = ""
    if phone:
        possible = [
            record for record in records
            if _same_phone(str(_properties(record).get("phone", "")), phone)
        ]
        reason = "phone"
    if not possible and first and last:
        possible = [
            record for record in records
            if str(_properties(record).get("firstname", "")).strip().casefold() == first
            and str(_properties(record).get("lastname", "")).strip().casefold() == last
        ]
        reason = "name_company"
    if len(possible) == 1:
        record = possible[0]
        return MatchDecision(
            "POSSIBLE_MATCH", str(record.get("id", "")), reason,
            "possible", record, tuple(possible),
        )
    if possible:
        return MatchDecision("AMBIGUOUS", reason=reason, confidence="conflict", candidates=tuple(possible))
    return MatchDecision("NO_MATCH")
