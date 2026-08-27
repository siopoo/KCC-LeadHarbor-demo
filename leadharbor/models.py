from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Lead:
    name: str
    market: str = ""
    office: str = ""
    company_type: str = ""
    contact_first_name: str = ""
    contact_last_name: str = ""
    website: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    country: str = ""
    category: str = ""
    description: str = ""
    source: str = ""
    source_url: str = ""
    signal: str = ""
    scale: str = ""
    current_lead_status: str = "Unchecked"
    score: int = 0
    matched_keywords: str = ""
    crawled_pages: list[str] = field(default_factory=list, repr=False)

    def as_csv_row(self) -> dict[str, object]:
        row = asdict(self)
        row.pop("office", None)
        row["crawled_pages"] = " | ".join(self.crawled_pages)
        return row
