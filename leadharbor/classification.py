from __future__ import annotations

import re

from .models import Lead


TYPE_RULES = (
    ("Multifamily", re.compile(r"\b(multifamily|multi-family|apartments?|developer|development)\b", re.I)),
    ("Remodeler", re.compile(r"\b(remodel(?:er|ing)?|renovation|renovator)\b", re.I)),
    ("Dealer", re.compile(r"\b(dealer|showroom|distributor|retailer|retail store)\b", re.I)),
    ("Builder", re.compile(r"\b(builder|homebuilder|home builder|homes)\b", re.I)),
    ("Contractor", re.compile(r"\b(contractor|contracting|construction|design.?build)\b", re.I)),
)

# The service area shown on KCC's territory map. Market values are always one
# of these state names; the complete street/city/state text belongs in address.
TARGET_STATES = (
    ("Florida", "FL"),
    ("Illinois", "IL"),
    ("Indiana", "IN"),
    ("Iowa", "IA"),
    ("Louisiana", "LA"),
    ("Michigan", "MI"),
    ("Minnesota", "MN"),
    ("Mississippi", "MS"),
    ("Ohio", "OH"),
    ("Texas", "TX"),
    ("Wisconsin", "WI"),
)
TARGET_STATE_NAMES = tuple(name for name, _ in TARGET_STATES)

# City-only legacy records can still be classified without inventing an
# address. New crawler records normally include a state in their address.
CITY_STATES = {
    "Chicago": "Illinois",
    "Madison": "Wisconsin",
    "Austin": "Texas",
    "San Antonio": "Texas",
    "Orlando": "Florida",
    "Miami": "Florida",
}

# Retained internally for the existing score bonus. Office is no longer an
# exported or visible company field.
OFFICE_STATE_RULES = {
    "KCC": (("Illinois", "IL"), ("Indiana", "IN"), ("Ohio", "OH"), ("Michigan", "MI")),
    "KCCWI": (("Wisconsin", "WI"), ("Minnesota", "MN")),
    "KCCFL": (("Florida", "FL"),),
}


def _has_state(location: str, name: str, code: str) -> bool:
    if name.casefold() in location.casefold():
        return True
    return bool(re.search(rf"(?:^|[,\s]){code}(?:$|[,\s])", location, re.I))


def infer_market(lead: Lead, requested_location: str = "") -> str:
    """Return the canonical target-state name, or empty outside the territory."""
    location = " ".join((lead.market, lead.address, lead.country, requested_location))
    for name, code in TARGET_STATES:
        if _has_state(location, name, code):
            return name
    for city, state in CITY_STATES.items():
        if re.search(rf"\b{re.escape(city)}\b", location, re.I):
            return state
    return ""


def is_target_market(lead: Lead, requested_location: str = "") -> bool:
    return bool(infer_market(lead, requested_location))


def infer_office(lead: Lead) -> str:
    state = infer_market(lead)
    if not state:
        return ""
    if state == "Texas" and re.search(
        r"\b(Austin|San\s+Antonio)\b", " ".join((lead.market, lead.address)), re.I
    ):
        return "KCC"
    for office, states in OFFICE_STATE_RULES.items():
        if any(state == name for name, _ in states):
            return office
    return ""


def infer_company_type(lead: Lead) -> str:
    text = " ".join((lead.name, lead.category, lead.description))
    for label, pattern in TYPE_RULES:
        if pattern.search(text):
            return label
    if lead.category and lead.category not in {"keyword search", "association member"}:
        return lead.category.replace("_", " ").title()
    return "Other"


def prepare_output_fields(lead: Lead, requested_location: str = "") -> Lead:
    original_market = lead.market.strip()
    assigned_market = infer_market(lead, requested_location)
    if original_market and original_market.casefold() != assigned_market.casefold() and not lead.address:
        lead.address = original_market
    lead.market = assigned_market
    if not lead.company_type:
        lead.company_type = infer_company_type(lead)
    lead.office = infer_office(lead)
    if not lead.current_lead_status:
        lead.current_lead_status = "Unchecked"
    return lead
