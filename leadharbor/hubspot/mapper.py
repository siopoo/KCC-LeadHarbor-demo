from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any

from .normalization import normalize_domain, normalize_email


COMPANY_PROPERTY_MAP = {
    "name": "name",
    "website": "website",
    "phone": "phone",
    "address": "address",
    "city": "city",
    "country": "country",
    "employee_count": "numberofemployees",
    "source": "leadharbor_source",
    "score": "leadharbor_score",
}

CONSTRUCTION_INDUSTRY_ALIASES = {
    "construction", "contractor", "general contractor", "retail contractor",
    "builder", "commercial builder", "remodeler",
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
    industry_options: Mapping[str, str] | None = None,
) -> dict[str, str]:
    mapped: dict[str, object] = {}
    for local_name, hubspot_name in COMPANY_PROPERTY_MAP.items():
        mapped[hubspot_name] = company.get(local_name, "")
    mapped["domain"] = normalize_domain(str(company.get("website") or company.get("domain") or ""))
    mapped["state"] = company.get("state") or company.get("market") or ""
    raw_industry = str(company.get("industry") or company.get("company_type") or "").strip()
    if raw_industry and industry_options:
        normalized = raw_industry.casefold()
        if normalized in CONSTRUCTION_INDUSTRY_ALIASES:
            normalized = "construction"
        mapped["industry"] = industry_options.get(normalized, "")
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
        "phone": company.get("contact_phone", ""),
        "leadharbor_contact_id": company.get("id", ""),
        "leadharbor_source": company.get("source", ""),
        "leadharbor_last_enriched_at": enriched_at,
    }
    return _clean_properties(mapped, available)
