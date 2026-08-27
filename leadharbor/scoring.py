from __future__ import annotations

import re

from .models import Lead
from .classification import infer_office


SCORING_VERSION = "icp-120-configurable-v3"
SCORING_TOTAL = 120
SCORING_SETTING_KEY = "scoring_weights"
DEFAULT_SCORING_WEIGHTS = {
    "icp_business_type": 25,
    "cabinet_relevance": 15,
    "active_opportunity": 25,
    "scale_potential": 15,
    "contactability": 10,
    "region_match": 5,
    "association_membership": 5,
    "office_assignment": 20,
}

APPROVED_REGIONS = {
    "FL": "florida", "IL": "illinois", "IN": "indiana",
    "IA": "iowa", "LA": "louisiana", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "OH": "ohio", "TX": "texas", "WI": "wisconsin",
}
ICP_TYPE = re.compile(
    r"\b(builder|homebuilder|remodel(?:er|ing)?|renovat(?:or|ion)|contractor|contracting|"
    r"construction|design.?build|cabinet(?:ry)?|kitchen|dealer|distributor|retailer|showroom)\b",
    re.I,
)
CABINET_RELEVANCE = re.compile(
    r"\b(cabinet(?:ry|s)?|kitchen\s+(?:products?|design|remodel(?:ing)?|dealer)|"
    r"casework|millwork|countertops?|vanit(?:y|ies))\b",
    re.I,
)
ACTIVE_SIGNAL = re.compile(
    r"\b(permit(?:s|ted)?|new\s+(?:project|community|communities|development|construction)|"
    r"groundbreaking|under\s+construction|recent(?:ly)?|renovation\s+project|remodel(?:ing)?\s+project|"
    r"awarded\s+(?:a\s+)?(?:contract|project)|project\s+pipeline)\b",
    re.I,
)
SCALE_POTENTIAL = re.compile(
    r"\b(multiple|multi-location|locations?|showrooms?|\d+[\s-]*(?:units?|homes?|projects?)|"
    r"multifamily|multi-family|commercial\s+projects?|large[-\s]scale|million|\$\s*\d+)\b",
    re.I,
)
ASSOCIATION_MEMBER = re.compile(
    r"\b(association\s+member|member\s+directory|industry\s+association|rca\s+member)\b",
    re.I,
)
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com",
}


def keyword_tokens(keyword: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"[\w\-]{3,}", keyword, re.UNICODE)}


def _all_text(lead: Lead) -> str:
    return " ".join(
        value for value in (
            lead.name, lead.company_type, lead.category, lead.description,
            lead.signal, lead.scale, lead.source,
        ) if value
    )


def _approved_region(lead: Lead) -> bool:
    location = " ".join((lead.market, lead.address, lead.country))
    lowered = location.casefold()
    if any(name in lowered for name in APPROVED_REGIONS.values()):
        return True
    return any(re.search(rf"(?:^|[,\s]){code}(?:$|[,\s])", location) for code in APPROVED_REGIONS)


def _business_email(email: str) -> bool:
    value = email.strip().casefold()
    if not EMAIL.match(value):
        return False
    return value.rsplit("@", 1)[-1] not in PUBLIC_EMAIL_DOMAINS


def validate_scoring_weights(values: dict[str, object]) -> dict[str, int]:
    if set(values) != set(DEFAULT_SCORING_WEIGHTS):
        raise ValueError("scoring rule fields are incomplete")
    try:
        weights = {key: int(values[key]) for key in DEFAULT_SCORING_WEIGHTS}
    except (TypeError, ValueError) as exc:
        raise ValueError("scoring rule values must be integers") from exc
    if any(value < 0 or value > SCORING_TOTAL for value in weights.values()):
        raise ValueError("scoring rule values are out of range")
    if sum(weights.values()) != SCORING_TOTAL:
        raise ValueError("scoring rule total must equal 120")
    return weights


def score_breakdown(lead: Lead, weights: dict[str, int] | None = None) -> dict[str, int]:
    """Return KCC's 100-point ICP score plus the 20-point Office bonus."""
    weights = validate_scoring_weights(weights or DEFAULT_SCORING_WEIGHTS)
    text = _all_text(lead)
    contact_score = 0
    if lead.website:
        contact_score += 2
    if _business_email(lead.email):
        contact_score += 3
    if lead.phone:
        contact_score += 2
    if lead.contact_first_name and lead.contact_last_name:
        contact_score += 3

    return {
        "icp_business_type": weights["icp_business_type"] if ICP_TYPE.search(text) else 0,
        "cabinet_relevance": weights["cabinet_relevance"] if CABINET_RELEVANCE.search(text) else 0,
        "active_opportunity": weights["active_opportunity"] if (lead.signal.strip() or ACTIVE_SIGNAL.search(text)) else 0,
        "scale_potential": weights["scale_potential"] if (lead.scale.strip() or SCALE_POTENTIAL.search(text)) else 0,
        "contactability": round(contact_score * weights["contactability"] / 10),
        "region_match": weights["region_match"] if _approved_region(lead) else 0,
        "association_membership": weights["association_membership"] if ASSOCIATION_MEMBER.search(text) else 0,
        "office_assignment": weights["office_assignment"] if (lead.office or infer_office(lead)) else 0,
    }


def score_lead(
    lead: Lead, keyword: str = "", weights: dict[str, int] | None = None,
) -> Lead:
    tokens = keyword_tokens(keyword)
    haystack = _all_text(lead).casefold()
    lead.matched_keywords = ", ".join(sorted(token for token in tokens if token in haystack))
    lead.score = sum(score_breakdown(lead, weights).values())
    return lead
