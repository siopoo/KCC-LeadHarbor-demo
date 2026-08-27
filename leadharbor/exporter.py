from __future__ import annotations

import csv
from pathlib import Path

from .models import Lead


def export_csv(leads: list[Lead], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(Lead(name="").as_csv_row().keys())
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(lead.as_csv_row() for lead in leads)
