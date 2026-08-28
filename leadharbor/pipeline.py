from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Protocol

from .classification import is_target_market, prepare_output_fields
from .crawler import WebsiteCrawler
from .exporter import export_csv
from .models import Lead
from .net import domain_key
from .scoring import score_lead
from .sources import OpenStreetMapSource

LOG = logging.getLogger(__name__)


class TaskCancelled(RuntimeError):
    pass


class DiscoverySource(Protocol):
    def discover(self, keyword: str, location: str, limit: int) -> list[Lead]:
        ...


class LeadPipeline:
    def __init__(
        self,
        pages_per_site: int = 4,
        request_delay: float = 1.0,
        crawl_websites: bool = True,
        sources: list[DiscoverySource] | None = None,
        scoring_weights: dict[str, int] | None = None,
    ) -> None:
        self.sources = sources or [OpenStreetMapSource()]
        self.crawler = WebsiteCrawler(max_pages=pages_per_site, delay=request_delay)
        self.crawl_websites = crawl_websites
        self.scoring_weights = scoring_weights

    @staticmethod
    def _deduplicate(leads: list[Lead]) -> list[Lead]:
        result: list[Lead] = []
        seen: dict[str, Lead] = {}
        for lead in leads:
            key = domain_key(lead.website) or lead.name.casefold().strip()
            if not key:
                continue
            if key not in seen:
                seen[key] = lead
                result.append(lead)
                continue
            existing = seen[key]
            for field in (
                "market", "company_type", "contact_first_name", "contact_last_name", "website",
                "email", "phone", "address", "country", "category", "description", "signal", "scale",
            ):
                if not getattr(existing, field) and getattr(lead, field):
                    setattr(existing, field, getattr(lead, field))
            for field in ("source", "source_url"):
                values = [value for value in (getattr(existing, field), getattr(lead, field)) if value]
                setattr(existing, field, " | ".join(dict.fromkeys(values)))
        return result

    def run(
        self, keyword: str, location: str, limit: int, output: Path,
        progress: Callable[[int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> list[Lead]:
        progress = progress or (lambda _value, _message: None)
        is_cancelled = is_cancelled or (lambda: False)
        candidates: list[Lead] = []
        discovery_errors: list[str] = []
        progress(5, "discovering")
        for source_index, source in enumerate(self.sources, start=1):
            if is_cancelled():
                raise TaskCancelled("task cancelled")
            try:
                candidates.extend(source.discover(keyword, location, limit * 2))
            except Exception as exc:
                LOG.warning("Discovery source %s failed: %s", type(source).__name__, exc)
                discovery_errors.append(f"{type(source).__name__}: {exc}")
            progress(5 + round(source_index * 15 / max(1, len(self.sources))), "discovering")
        if not candidates and discovery_errors:
            raise RuntimeError("; ".join(discovery_errors))
        leads = self._deduplicate(candidates)[:limit]
        LOG.info("Found %d unique candidate businesses", len(leads))
        accepted: list[Lead] = []
        for index, lead in enumerate(leads, start=1):
            if is_cancelled():
                raise TaskCancelled("task cancelled")
            progress(20 + round((index - 1) * 70 / max(1, len(leads))), f"processing:{index}:{len(leads)}")
            LOG.info("Processing %d/%d: %s", index, len(leads), lead.name)
            if self.crawl_websites and lead.website:
                self.crawler.enrich(lead)
            prepare_output_fields(lead, location)
            if not is_target_market(lead):
                LOG.info("Filtered outside target market: %s", lead.name)
                continue
            score_lead(lead, keyword, self.scoring_weights)
            accepted.append(lead)
        if is_cancelled():
            raise TaskCancelled("task cancelled")
        accepted.sort(key=lambda item: item.score, reverse=True)
        progress(92, "exporting")
        export_csv(accepted, output)
        progress(96, "saving")
        return accepted
