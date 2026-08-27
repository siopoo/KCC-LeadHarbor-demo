from __future__ import annotations

import logging
import time
from collections import deque
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from .extractor import extract_page, merge_page_data
from .models import Lead
from .net import is_public_http_url, normalize_url
from .sources import USER_AGENT

LOG = logging.getLogger(__name__)


class WebsiteCrawler:
    def __init__(self, max_pages: int = 4, delay: float = 1.0, timeout: float = 15) -> None:
        self.max_pages = max_pages
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})

    def _robots(self, url: str) -> RobotFileParser:
        parsed = urlparse(url)
        robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
        parser = RobotFileParser(robots_url)
        try:
            response = self.session.get(robots_url, timeout=self.timeout)
            if response.ok:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])
        except requests.RequestException:
            parser.parse([])
        return parser

    def enrich(self, lead: Lead) -> Lead:
        start = normalize_url(lead.website)
        if not start or not is_public_http_url(start):
            return lead
        robots = self._robots(start)
        queue: deque[str] = deque([start])
        seen: set[str] = set()

        while queue and len(seen) < self.max_pages:
            url = queue.popleft()
            if url in seen or not robots.can_fetch(USER_AGENT, url):
                continue
            seen.add(url)
            try:
                LOG.info("Crawling %s", url)
                response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                response.raise_for_status()
                if "text/html" not in response.headers.get("Content-Type", ""):
                    continue
                data, links = extract_page(response.text, response.url)
                merge_page_data(lead, data)
                lead.crawled_pages.append(response.url)
                queue.extend(link for link in links if link not in seen)
            except requests.RequestException as exc:
                LOG.warning("Could not crawl %s: %s", url, exc)
            if queue and self.delay:
                time.sleep(self.delay)
        return lead
