from __future__ import annotations

import re
from urllib.parse import urlparse

import requests

from .models import Lead
from .net import normalize_url
from .sources import USER_AGENT

BLOCKED_HOSTS = {
    "facebook.com", "instagram.com", "linkedin.com", "x.com", "twitter.com",
    "youtube.com", "yelp.com", "yellowpages.com", "mapquest.com",
    "procore.com",
}
GENERIC_DIRECTORY_TITLE = re.compile(
    r"^(?:find|browse|search|top|best|list(?:\s+of)?)\b.*\b"
    r"(?:contractors?|builders?|remodelers?|construction companies)\b(?:\s+in\b|\s+near\b|$)",
    re.I,
)


class BraveSearchSource:
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, queries: list[str] | None = None, timeout: float = 30) -> None:
        if not api_key:
            raise ValueError("A Brave Search API key is required")
        self.api_key = api_key
        self.queries = queries or []
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
            "User-Agent": USER_AGENT,
        })

    @staticmethod
    def default_queries(keyword: str, location: str) -> list[str]:
        return [
            f'"{keyword}" contractor {location}',
            f'"{keyword}" builder {location}',
            f'"retail construction" "general contractor" {location}',
        ]

    def discover(self, keyword: str, location: str, limit: int) -> list[Lead]:
        queries = self.queries or self.default_queries(keyword, location)
        leads: list[Lead] = []
        per_query = min(20, max(5, limit))
        for query in queries:
            response = self.session.get(
                self.endpoint,
                params={"q": query, "count": per_query, "search_lang": "en"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            for item in response.json().get("web", {}).get("results", []):
                lead = self._to_lead(item, query)
                if lead:
                    leads.append(lead)
                if len(leads) >= limit:
                    return leads
        return leads

    @staticmethod
    def _to_lead(item: dict, query: str) -> Lead | None:
        url = normalize_url(str(item.get("url", "")))
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not url or any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_HOSTS):
            return None
        title = re.split(r"\s+[|\-–—]\s+", str(item.get("title", "")), maxsplit=1)[0].strip()
        if not title or GENERIC_DIRECTORY_TITLE.search(title):
            return None
        return Lead(
            name=title,
            website=url,
            description=str(item.get("description", "")).strip(),
            source="Brave Search",
            source_url=url,
            category="keyword search",
            matched_keywords=query,
        )
