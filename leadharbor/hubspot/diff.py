from __future__ import annotations

from collections.abc import Mapping

from .models import FieldDifference


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def compare_properties(
    hubspot: Mapping[str, object], leadharbor: Mapping[str, object],
) -> list[FieldDifference]:
    """Describe a safe fill-missing-only patch without sending empty local values."""
    differences: list[FieldDifference] = []
    for field, local_value in leadharbor.items():
        local = _clean(local_value)
        if not local:
            continue
        remote = _clean(hubspot.get(field))
        if not remote:
            action = "FILL_MISSING"
        elif remote.casefold() == local.casefold():
            action = "NO_CHANGE"
        else:
            action = "CONFLICT"
        differences.append(FieldDifference(field, remote, local, action))
    return differences


def fill_missing_patch(differences: list[FieldDifference]) -> dict[str, str]:
    return {
        difference.field: difference.leadharbor_value
        for difference in differences
        if difference.action == "FILL_MISSING"
    }
