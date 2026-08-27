from __future__ import annotations

import csv
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from .models import Lead
from .net import is_public_http_url, normalize_url
from .sources import USER_AGENT

RCA_DIRECTORY_URL = (
    "https://retailgcs.memberclicks.net/assets/Directory/"
    "RCA_2024Directory_Final_20250103.pdf"
)
ASSOCIATION_PRESETS = {"rca": RCA_DIRECTORY_URL}

COMPANY_HINTS = re.compile(
    r"\b(construction|contractors?|builders?|building|design.?build|enterprises?|"
    r"commercial|group|company|co\.?|inc\.?|llc|ltd\.?)\b",
    re.I,
)
SKIP_HOSTS = {
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com",
    "twitter.com", "x.com",
}
SKIP_TEXT = re.compile(
    r"^(home|about|contact|login|log in|join|events?|news|privacy|terms|search|"
    r"next|previous|find a contractor)$",
    re.I,
)

EMAIL_LINE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
PHONE_LINE = re.compile(r"^(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}$")
LOCATION_LINE = re.compile(r"^.+,\s*[A-Z]{2}$")
ROLE_LINE = re.compile(
    r"\b(president|chief|c\.?e\.?o\.?|owner|principal|director|vice|operations|executive|"
    r"member|partner|development|officer|retail construction)\b",
    re.I,
)
NON_COMPANY_TEXT = re.compile(
    r"\b(at least|years? of experience|employed by|current member|requirements?|"
    r"training|program|curriculum|course|certificate|certification|must be|shall be|"
    r"customer focused|project focused|leadership|communication)\b",
    re.I,
)
EMAIL_ANY = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
PHONE_ANY = re.compile(r"(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}")
WEBSITE_LINE = re.compile(r"^(?:https?://)?(?:www\.)?[A-Z0-9-]+(?:\.[A-Z0-9-]+)+(?:/\S*)?$", re.I)
PDF_NOISE = re.compile(
    r"\b(member directory|membership directory|table of contents|copyright|all rights reserved|"
    r"page \d+|www\.|https?://)\b",
    re.I,
)


class AssociationSource:
    def __init__(
        self,
        urls: list[str] | None = None,
        csv_paths: list[Path] | None = None,
        timeout: float = 30,
    ) -> None:
        self.urls = urls or []
        self.csv_paths = csv_paths or []
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})

    def discover(self, keyword: str, location: str, limit: int) -> list[Lead]:
        leads: list[Lead] = []
        for path in self.csv_paths:
            leads.extend(self._read_csv(path))
        for url in self.urls:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            leads.extend(self.parse_html(response.text, response.url))
        return leads[:limit]

    @staticmethod
    def _read_csv(path: Path) -> list[Lead]:
        leads: list[Lead] = []
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                normalized = {str(key).strip().casefold(): (value or "").strip() for key, value in row.items()}
                name = normalized.get("company") or normalized.get("company_name") or normalized.get("name") or normalized.get("公司名称")
                if not name:
                    continue
                website = normalized.get("website") or normalized.get("url") or normalized.get("官网") or ""
                raw_score = normalized.get("score") or normalized.get("评分") or "0"
                try:
                    score = max(0, min(120, int(raw_score)))
                except ValueError:
                    score = 0
                leads.append(Lead(
                    name=name,
                    market=normalized.get("market") or normalized.get("市场") or "",
                    company_type=normalized.get("type") or normalized.get("company_type") or normalized.get("类型") or "",
                    contact_first_name=normalized.get("contact first name") or normalized.get("contact_first_name") or normalized.get("联系人名") or "",
                    contact_last_name=normalized.get("contact last name") or normalized.get("contact_last_name") or normalized.get("联系人姓") or "",
                    website=normalize_url(website),
                    email=normalized.get("contact info") or normalized.get("email") or normalized.get("邮箱") or "",
                    phone=normalized.get("phone number (if available)") or normalized.get("phone") or normalized.get("电话") or "",
                    address=normalized.get("address") or normalized.get("地址") or "",
                    category="association member",
                    source=f"Association CSV: {path.name}",
                    source_url=str(path),
                    signal=normalized.get("signal") or normalized.get("业务信号") or "",
                    scale=normalized.get("scale") or normalized.get("规模") or "",
                    score=score,
                ))
        return leads

    @staticmethod
    def parse_html(html: str, page_url: str) -> list[Lead]:
        return RcaDirectorySource.parse_html(html, page_url)


class RcaDirectorySource:
    """Read the RCA's official public member-directory PDF."""

    def __init__(self, url: str = RCA_DIRECTORY_URL, timeout: float = 60) -> None:
        self.url = url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/pdf"})

    def discover(self, keyword: str, location: str, limit: int) -> list[Lead]:
        response = self.session.get(self.url, timeout=self.timeout)
        response.raise_for_status()
        pages = [page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages]
        leads = self.parse_pages(pages)
        if not leads:
            raise ValueError("RCA official directory was downloaded but no member records were recognized")
        return leads[:limit]

    @classmethod
    def parse_pages(cls, pages: list[str]) -> list[Lead]:
        leads: list[Lead] = []
        seen: set[str] = set()
        for page in pages:
            for lead in cls._parse_member_page(page):
                key = lead.name.casefold()
                if key not in seen:
                    seen.add(key)
                    leads.append(lead)
        return leads

    @staticmethod
    def _parse_member_page(text: str) -> list[Lead]:
        return PdfAssociationSource._parse_member_page(text)

    @staticmethod
    def parse_html(html: str, page_url: str) -> list[Lead]:
        return PdfAssociationSource.parse_html(html, page_url)


class PdfAssociationSource:
    """Extract association members from a public or uploaded text-based PDF."""

    def __init__(
        self,
        association_name: str,
        pdf_path: Path,
        source_url: str = "",
        max_pages: int = 300,
    ) -> None:
        self.association_name = association_name.strip() or "Association"
        self.pdf_path = Path(pdf_path)
        self.source_url = source_url
        self.max_pages = max_pages

    @staticmethod
    def download_url(url: str, max_bytes: int = 20 * 1024 * 1024) -> bytes:
        current = normalize_url(url)
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/pdf"})
        for _ in range(5):
            if not current or not is_public_http_url(current):
                raise ValueError("PDF URL must be a public HTTP or HTTPS address")
            response = session.get(current, timeout=60, stream=True, allow_redirects=False)
            if response.is_redirect or response.is_permanent_redirect:
                destination = response.headers.get("Location", "")
                response.close()
                current = urljoin(current, destination)
                continue
            response.raise_for_status()
            content_length = int(response.headers.get("Content-Length", "0") or 0)
            if content_length > max_bytes:
                response.close()
                raise ValueError("PDF is larger than 20 MB")
            payload = bytearray()
            for chunk in response.iter_content(64 * 1024):
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    response.close()
                    raise ValueError("PDF is larger than 20 MB")
            response.close()
            data = bytes(payload)
            if not data.startswith(b"%PDF"):
                raise ValueError("URL did not return a PDF")
            return data
        raise ValueError("PDF URL redirected too many times")

    def discover(self, keyword: str, location: str, limit: int) -> list[Lead]:
        with self.pdf_path.open("rb") as stream:
            reader = PdfReader(stream)
            if len(reader.pages) > self.max_pages:
                raise ValueError(f"PDF exceeds the {self.max_pages}-page limit")
            pages = [page.extract_text() or "" for page in reader.pages]
        return self.parse_pages(
            pages, self.association_name, self.source_url or str(self.pdf_path)
        )[:limit]

    @classmethod
    def parse_pages(cls, pages: list[str], association_name: str, source_url: str = "") -> list[Lead]:
        leads: list[Lead] = []
        seen: set[str] = set()
        for page in pages:
            lines = [re.sub(r"\s+", " ", line).strip(" |•\t") for line in page.splitlines()]
            lines = [line for line in lines if line]
            email_indexes = [index for index, line in enumerate(lines) if EMAIL_ANY.search(line)]
            previous_email = -1
            for email_index in email_indexes:
                email_match = EMAIL_ANY.search(lines[email_index])
                if not email_match:
                    continue
                block = lines[previous_email + 1:email_index]
                previous_email = email_index
                cleaned = [
                    line for line in block
                    if not WEBSITE_LINE.fullmatch(line)
                    and not PHONE_ANY.search(line)
                    and not PDF_NOISE.search(line)
                ]
                market = next((line for line in reversed(block) if LOCATION_LINE.fullmatch(line)), "")
                phone = next((match.group(0) for line in reversed(block) if (match := PHONE_ANY.search(line))), "")
                before_market = cleaned
                if market in before_market:
                    before_market = before_market[:before_market.index(market)]
                candidates = [line.strip(" ,") for line in before_market if len(line) <= 140]
                company_index = next(
                    (index for index in range(len(candidates) - 1, -1, -1) if COMPANY_HINTS.search(candidates[index])),
                    None,
                )
                if company_index is None and candidates:
                    company_index = max(0, len(candidates) - 3)
                if company_index is None:
                    continue
                company = candidates[company_index]
                if NON_COMPANY_TEXT.search(company) or ROLE_LINE.fullmatch(company):
                    continue
                contact = ""
                role = ""
                for line in candidates[company_index + 1:]:
                    if ROLE_LINE.search(line):
                        role = line
                    elif not contact and 2 <= len(line.split()) <= 5 and not COMPANY_HINTS.search(line):
                        contact = line
                website = next(
                    (normalize_url(lines[index]) for index in range(email_index + 1, min(len(lines), email_index + 4)) if WEBSITE_LINE.fullmatch(lines[index])),
                    "",
                )
                key = company.casefold()
                if key in seen:
                    continue
                seen.add(key)
                name_parts = contact.rstrip(" ,").split()
                leads.append(Lead(
                    name=company,
                    market=market,
                    contact_first_name=name_parts[0] if len(name_parts) >= 2 else "",
                    contact_last_name=" ".join(name_parts[1:]) if len(name_parts) >= 2 else "",
                    website=website,
                    email=email_match.group(0),
                    phone=phone,
                    address=market,
                    category=f"{association_name} association member",
                    description=role,
                    source=f"{association_name} PDF member directory",
                    source_url=source_url,
                ))
        return leads

    @staticmethod
    def _parse_member_page(text: str) -> list[Lead]:
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        header = next(
            (i for i, line in enumerate(lines) if "Retail Contractors Association Members" in line),
            None,
        )
        if header is not None:
            lines = lines[header + 1:]
        leads: list[Lead] = []
        for index, line in enumerate(lines):
            if not EMAIL_LINE.fullmatch(line) or index < 3:
                continue
            if not PHONE_LINE.fullmatch(lines[index - 1]) or not LOCATION_LINE.fullmatch(lines[index - 2]):
                continue
            website = lines[index + 1] if index + 1 < len(lines) else ""
            if not re.match(r"^(?:https?://)?(?:www\.)?[A-Z0-9-]+(?:\.[A-Z0-9-]+)+/?$", website, re.I):
                continue

            prefix = lines[:index - 2]
            if len(prefix) < 2:
                continue
            if ROLE_LINE.search(prefix[-1]):
                role = prefix[-1]
                contact = prefix[-2]
                company_lines = prefix[:-2]
            else:
                role = ""
                contact = prefix[-1]
                company_lines = prefix[:-1]
            if not company_lines:
                continue
            company_parts = [company_lines[-1]]
            if len(company_lines) > 1 and re.match(
                r"^(?:commercial\s+construction|construction|development|of\b)",
                company_lines[-1],
                re.I,
            ):
                company_parts.insert(0, company_lines[-2])
            company = " ".join(company_parts).strip()
            contact = contact.rstrip(" ,")
            name_parts = contact.split()
            if not company or len(name_parts) < 2:
                continue
            leads.append(Lead(
                name=company,
                market=lines[index - 2],
                contact_first_name=name_parts[0],
                contact_last_name=" ".join(name_parts[1:]),
                website=normalize_url(website),
                email=line,
                phone=lines[index - 1],
                address=lines[index - 2],
                category="RCA association member",
                description=role,
                source="Retail Contractors Association member directory",
                source_url=RCA_DIRECTORY_URL,
            ))
        return leads

    @staticmethod
    def parse_html(html: str, page_url: str) -> list[Lead]:
        soup = BeautifulSoup(html, "html.parser")
        association_host = (urlparse(page_url).hostname or "").lower().removeprefix("www.")
        leads: list[Lead] = []
        seen_names: set[str] = set()

        def add(name: str, website: str = "") -> None:
            cleaned = re.sub(r"\s+", " ", name).strip(" •|,;\n\t")
            key = cleaned.casefold()
            if not cleaned or len(cleaned) > 120 or SKIP_TEXT.match(cleaned) or key in seen_names:
                return
            if NON_COMPANY_TEXT.search(cleaned) or len(cleaned.split()) > 12:
                return
            if not COMPANY_HINTS.search(cleaned):
                return
            seen_names.add(key)
            leads.append(Lead(
                name=cleaned,
                website=normalize_url(website),
                category="association member",
                source=f"Association: {association_host}",
                source_url=page_url,
            ))

        for anchor in soup.select("a[href]"):
            href = urljoin(page_url, str(anchor.get("href", "")))
            host = (urlparse(href).hostname or "").lower().removeprefix("www.")
            text = anchor.get_text(" ", strip=True)
            is_external = host and host != association_host
            is_skipped = any(host == item or host.endswith("." + item) for item in SKIP_HOSTS)
            if is_external and not is_skipped:
                add(text, href)

        for element in soup.select("li, tr, .member, .member-name, .company, .company-name"):
            text = element.get_text(" ", strip=True)
            if len(text) <= 120:
                add(text)
        return leads
