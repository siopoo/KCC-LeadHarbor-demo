from __future__ import annotations

import json
import re
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .models import Lead

EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d().\s\-]{7,}\d)(?!\w)")
INTERESTING_PATHS = (
    "contact", "contact-us", "about", "about-us", "company", "products",
    "services", "imprint", "legal", "kontakt", "uber-uns", "company-profile",
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean(value).strip(" ,;|")
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _jsonld_objects(soup: BeautifulSoup) -> Iterable[dict]:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (TypeError, json.JSONDecodeError):
            continue
        queue = data if isinstance(data, list) else [data]
        for item in queue:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                queue.extend(graph)
            yield item


def extract_page(html: str, url: str) -> tuple[dict[str, str], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    title = _clean(soup.title.get_text(" ") if soup.title else "")
    name = re.split(r"\s+[|\-–—]\s+", title, maxsplit=1)[0]
    description_tag = soup.select_one('meta[name="description"], meta[property="og:description"]')
    description = _clean(description_tag.get("content", "") if description_tag else "")
    text = _clean(soup.get_text(" "))
    emails = _unique(EMAIL_RE.findall(text))
    phones = _unique(PHONE_RE.findall(text))

    for item in _jsonld_objects(BeautifulSoup(html, "html.parser")):
        item_type = str(item.get("@type", "")).casefold()
        if any(word in item_type for word in ("organization", "business", "corporation")):
            name = _clean(str(item.get("name", ""))) or name
            description = _clean(str(item.get("description", ""))) or description
            emails.extend(_unique([str(item.get("email", ""))]))
            phones.extend(_unique([str(item.get("telephone", ""))]))

    base_host = (urlparse(url).hostname or "").removeprefix("www.")
    links: list[str] = []
    for anchor in soup.select("a[href]"):
        absolute = urljoin(url, str(anchor.get("href", ""))).split("#", 1)[0]
        parsed = urlparse(absolute)
        host = (parsed.hostname or "").removeprefix("www.")
        hint = (parsed.path + " " + anchor.get_text(" ")).casefold()
        if parsed.scheme in {"http", "https"} and host == base_host:
            if any(part in hint for part in INTERESTING_PATHS):
                links.append(absolute)

    return {
        "name": name,
        "description": description,
        "email": ", ".join(_unique(emails)),
        "phone": ", ".join(_unique(phones)),
    }, _unique(links)


def merge_page_data(lead: Lead, data: dict[str, str]) -> None:
    if not lead.name and data.get("name"):
        lead.name = data["name"]
    if not lead.description and data.get("description"):
        lead.description = data["description"]
    lead.email = ", ".join(_unique([*lead.email.split(", "), *data.get("email", "").split(", ")]))
    lead.phone = ", ".join(_unique([*lead.phone.split(", "), *data.get("phone", "").split(", ")]))
