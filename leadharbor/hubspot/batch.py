from __future__ import annotations

from typing import Any

from .client import API_VERSION, HubSpotClient, HubSpotError, chunked


def _trace_id(item: dict[str, Any]) -> str:
    direct = item.get("objectWriteTraceId")
    if direct:
        return str(direct)
    context = item.get("context")
    if isinstance(context, dict):
        value = context.get("objectWriteTraceId")
        if isinstance(value, list) and value:
            return str(value[0])
        if value:
            return str(value)
    return ""


def batch_create(
    client: HubSpotClient, object_type: str, items: list[tuple[str, dict[str, str]]],
) -> dict[str, dict[str, Any]]:
    outcomes: dict[str, dict[str, Any]] = {}
    for group in chunked(items, 100):
        inputs = [
            {"objectWriteTraceId": key, "properties": properties}
            for key, properties in group
        ]
        try:
            payload = client.request(
                "POST", f"/crm/objects/{API_VERSION}/{object_type}/batch/create",
                json={"inputs": inputs},
            )
        except HubSpotError as exc:
            outcomes.update({key: {"error": str(exc)} for key, _ in group})
            continue
        unclaimed = list(group)
        for index, record in enumerate(payload.get("results", [])):
            key = _trace_id(record)
            if not key and index < len(unclaimed):
                key = unclaimed[index][0]
            if key:
                outcomes[key] = {"record": record}
        for error in payload.get("errors", []):
            key = _trace_id(error)
            if key:
                outcomes[key] = {"error": str(error.get("message", "HubSpot rejected this record."))[:500]}
        for key, _ in group:
            outcomes.setdefault(key, {"error": "HubSpot did not return a result for this record."})
    return outcomes


def batch_read(
    client: HubSpotClient,
    object_type: str,
    record_ids: list[str],
    properties: list[str],
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    unique_ids = list(dict.fromkeys(record_id for record_id in record_ids if record_id))
    for group in chunked(unique_ids, 100):
        payload = client.request(
            "POST", f"/crm/objects/{API_VERSION}/{object_type}/batch/read",
            json={
                "properties": properties,
                "inputs": [{"id": record_id} for record_id in group],
            },
        )
        for record in payload.get("results", []):
            record_id = str(record.get("id", ""))
            if record_id:
                records[record_id] = record
    return records


def batch_update(
    client: HubSpotClient,
    object_type: str,
    items: list[tuple[str, str, dict[str, str]]],
) -> dict[str, dict[str, Any]]:
    outcomes: dict[str, dict[str, Any]] = {}
    for group in chunked(items, 100):
        inputs = [
            {"objectWriteTraceId": key, "id": record_id, "properties": properties}
            for key, record_id, properties in group
        ]
        try:
            payload = client.request(
                "POST", f"/crm/objects/{API_VERSION}/{object_type}/batch/update",
                json={"inputs": inputs},
            )
        except HubSpotError as exc:
            outcomes.update({key: {"error": str(exc)} for key, _, _ in group})
            continue
        for index, record in enumerate(payload.get("results", [])):
            key = _trace_id(record) or (group[index][0] if index < len(group) else "")
            if key:
                outcomes[key] = {"record": record}
        for error in payload.get("errors", []):
            key = _trace_id(error)
            if key:
                outcomes[key] = {"error": str(error.get("message", "HubSpot rejected this record."))[:500]}
        for key, _, _ in group:
            outcomes.setdefault(key, {"error": "HubSpot did not return a result for this record."})
    return outcomes
