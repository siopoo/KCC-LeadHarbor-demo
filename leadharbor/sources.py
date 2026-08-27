from __future__ import annotations

import logging
import re
from typing import Any

import requests

from .models import Lead
from .net import normalize_url

LOG = logging.getLogger(__name__)
USER_AGENT = "KCC-LeadHarbor/0.1 (public-business-research)"


class OpenStreetMapSource:
    geocoder_url = "https://nominatim.openstreetmap.org/search"
    overpass_url = "https://overpass-api.de/api/interpreter"

    def __init__(self, timeout: float = 45) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    def _bounding_box(self, location: str) -> tuple[float, float, float, float]:
        response = self.session.get(
            self.geocoder_url,
            params={"q": location, "format": "jsonv2", "limit": 1},
            timeout=self.timeout,
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            raise ValueError(f"Location not found: {location}")
        south, north, west, east = map(float, results[0]["boundingbox"])
        return south, west, north, east

    @staticmethod
    def _keyword_pattern(keyword: str) -> str:
        words = re.findall(r"[\w\-]{3,}", keyword, flags=re.UNICODE)
        if not words:
            raise ValueError("Keyword must contain at least one meaningful word")
        return "|".join(re.escape(word) for word in words[:8])

    def discover(self, keyword: str, location: str, limit: int) -> list[Lead]:
        south, west, north, east = self._bounding_box(location)
        pattern = self._keyword_pattern(keyword)
        bbox = f"{south},{west},{north},{east}"
        fields = ("name", "description", "operator", "brand", "shop", "craft", "office", "industrial")
        selectors = "\n".join(
            f'nwr["{field}"~"{pattern}",i]({bbox});' for field in fields
        )
        query = f"[out:json][timeout:40];({selectors});out center tags {max(limit * 3, 30)};"
        LOG.info("Discovering businesses from OpenStreetMap in %s", location)
        response = self.session.post(self.overpass_url, data={"data": query}, timeout=self.timeout)
        response.raise_for_status()
        leads = [self._to_lead(item) for item in response.json().get("elements", [])]
        return [lead for lead in leads if lead.name][:limit]

    @staticmethod
    def _to_lead(item: dict[str, Any]) -> Lead:
        tags = item.get("tags", {})
        website = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
        email = tags.get("email") or tags.get("contact:email") or ""
        phone = tags.get("phone") or tags.get("contact:phone") or ""
        address_parts = [
            tags.get("addr:housenumber", ""), tags.get("addr:street", ""),
            tags.get("addr:city", ""), tags.get("addr:state", ""), tags.get("addr:postcode", ""),
        ]
        category = next(
            (tags.get(key, "") for key in ("industrial", "craft", "office", "shop", "amenity") if tags.get(key)),
            "",
        )
        osm_type = item.get("type", "node")
        osm_id = item.get("id", "")
        return Lead(
            name=tags.get("name") or tags.get("operator") or tags.get("brand") or "",
            website=normalize_url(website),
            email=email,
            phone=phone,
            address=" ".join(part for part in address_parts if part),
            country=tags.get("addr:country", ""),
            category=category,
            description=tags.get("description", ""),
            source="OpenStreetMap",
            source_url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        )
