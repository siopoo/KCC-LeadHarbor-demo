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
EMAIL_IN_TEXT = re.compile(r"[^\s,;@]+@[^\s,;@]+\.[^\s,;@]+")
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


def _business_emails(email: str) -> list[str]:
    values: list[str] = []
    for match in EMAIL_IN_TEXT.findall(email or ""):
        value = match.strip().casefold()
        if EMAIL.match(value) and value.rsplit("@", 1)[-1] not in PUBLIC_EMAIL_DOMAINS:
            if value not in values:
                values.append(value)
    return values


def _evidence(*values: str) -> list[str]:
    result: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", value or "").strip(" ,;|")
        if cleaned and cleaned.casefold() not in {item.casefold() for item in result}:
            result.append(cleaned[:220])
    return result


def _pattern_evidence(pattern: re.Pattern[str], text: str) -> list[str]:
    return _evidence(*(match.group(0) for match in list(pattern.finditer(text))[:3]))


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


def score_details(
    lead: Lead, weights: dict[str, int] | None = None,
) -> dict[str, dict[str, object]]:
    """Return each rule's points, maximum points, and supporting evidence."""
    weights = validate_scoring_weights(weights or DEFAULT_SCORING_WEIGHTS)
    text = _all_text(lead)
    business_emails = _business_emails(lead.email)
    contact_score = 0
    if lead.website:
        contact_score += 2
    if business_emails:
        contact_score += 3
    if lead.phone:
        contact_score += 2
    if lead.contact_first_name and lead.contact_last_name:
        contact_score += 3

    icp_match = bool(ICP_TYPE.search(text))
    cabinet_match = bool(CABINET_RELEVANCE.search(text))
    active_match = bool(lead.signal.strip() or ACTIVE_SIGNAL.search(text))
    scale_match = bool(lead.scale.strip() or SCALE_POTENTIAL.search(text))
    region_match = _approved_region(lead)
    association_match = bool(ASSOCIATION_MEMBER.search(text))
    office = lead.office or infer_office(lead)

    points = {
        "icp_business_type": weights["icp_business_type"] if icp_match else 0,
        "cabinet_relevance": weights["cabinet_relevance"] if cabinet_match else 0,
        "active_opportunity": weights["active_opportunity"] if active_match else 0,
        "scale_potential": weights["scale_potential"] if scale_match else 0,
        "contactability": round(contact_score * weights["contactability"] / 10),
        "region_match": weights["region_match"] if region_match else 0,
        "association_membership": weights["association_membership"] if association_match else 0,
        "office_assignment": weights["office_assignment"] if office else 0,
    }
    evidence = {
        "icp_business_type": _evidence(
            lead.company_type, lead.category, *_pattern_evidence(ICP_TYPE, text)
        ) if icp_match else [],
        "cabinet_relevance": _pattern_evidence(CABINET_RELEVANCE, text),
        "active_opportunity": _evidence(
            lead.signal, *_pattern_evidence(ACTIVE_SIGNAL, text)
        ) if active_match else [],
        "scale_potential": _evidence(
            lead.scale, *_pattern_evidence(SCALE_POTENTIAL, text)
        ) if scale_match else [],
        "contactability": _evidence(
            lead.website, *business_emails, lead.phone,
            " ".join((lead.contact_first_name, lead.contact_last_name)).strip(),
        ),
        "region_match": _evidence(lead.market, lead.address) if region_match else [],
        "association_membership": _evidence(
            lead.source, lead.category, *_pattern_evidence(ASSOCIATION_MEMBER, text)
        ) if association_match else [],
        "office_assignment": _evidence(office),
    }
    return {
        key: {
            "points": points[key],
            "max_points": weights[key],
            "evidence": evidence[key],
        }
        for key in DEFAULT_SCORING_WEIGHTS
    }


def score_breakdown(lead: Lead, weights: dict[str, int] | None = None) -> dict[str, int]:
    """Return KCC's 100-point ICP score plus the 20-point assignment bonus."""
    return {
        key: int(detail["points"])
        for key, detail in score_details(lead, weights).items()
    }


def score_lead(
    lead: Lead, keyword: str = "", weights: dict[str, int] | None = None,
) -> Lead:
    tokens = keyword_tokens(keyword)
    haystack = _all_text(lead).casefold()
    lead.matched_keywords = ", ".join(sorted(token for token in tokens if token in haystack))
    lead.score = sum(score_breakdown(lead, weights).values())
    return lead
