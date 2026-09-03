from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any

from .normalization import normalize_domain, normalize_email


COMPANY_PROPERTY_MAP = {
    "name": "name",
    "website": "website",
    "phone": "phone",
    "address": "address",
    "market": "state",
    "country": "country",
    "company_type": "industry",
    "employee_count": "numberofemployees",
    "source": "leadharbor_source",
    "score": "leadharbor_score",
}

CONTACT_PROPERTY_MAP = {
    "email": "email",
    "contact_first_name": "firstname",
    "contact_last_name": "lastname",
    "job_title": "jobtitle",
}

CUSTOM_COMPANY_PROPERTIES = {
    "leadharbor_company_id", "leadharbor_source", "leadharbor_score",
    "leadharbor_last_enriched_at",
}
CUSTOM_CONTACT_PROPERTIES = {
    "leadharbor_contact_id", "leadharbor_source", "leadharbor_last_enriched_at",
}


def _clean_properties(values: Mapping[str, object], available: Set[str]) -> dict[str, str]:
    return {
        key: str(value).strip()
        for key, value in values.items()
        if key in available and value is not None and str(value).strip()
    }


def map_company_properties(
    company: Mapping[str, Any], *, available: Set[str], enriched_at: str = "",
) -> dict[str, str]:
    mapped: dict[str, object] = {}
    for local_name, hubspot_name in COMPANY_PROPERTY_MAP.items():
        mapped[hubspot_name] = company.get(local_name, "")
    mapped["domain"] = normalize_domain(str(company.get("website") or company.get("domain") or ""))
    mapped["leadharbor_company_id"] = company.get("id", "")
    mapped["leadharbor_last_enriched_at"] = enriched_at
    return _clean_properties(mapped, available)


def map_contact_properties(
    company: Mapping[str, Any], *, available: Set[str], enriched_at: str = "",
) -> dict[str, str]:
    email = normalize_email(str(company.get("email", "")))
    first = str(company.get("contact_first_name", "")).strip()
    last = str(company.get("contact_last_name", "")).strip()
    if not email and not first and not last:
        return {}
    mapped: dict[str, object] = {
        "email": email,
        "firstname": first,
        "lastname": last,
        "jobtitle": company.get("job_title", ""),
        "phone": company.get("phone", ""),
        "leadharbor_contact_id": company.get("id", ""),
        "leadharbor_source": company.get("source", ""),
        "leadharbor_last_enriched_at": enriched_at,
    }
    return _clean_properties(mapped, available)
