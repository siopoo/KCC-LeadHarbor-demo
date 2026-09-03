from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class MatchDecision:
    kind: str
    record_id: str = ""
    reason: str = ""
    confidence: str = "none"
    record: dict[str, Any] | None = None
    candidates: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FieldDifference:
    field: str
    hubspot_value: str
    leadharbor_value: str
    action: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)
