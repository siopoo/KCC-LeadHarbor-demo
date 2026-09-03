from __future__ import annotations

from typing import Any

from .client import API_VERSION, HubSpotClient, HubSpotError
from .batch import batch_create, batch_read, batch_update
from .normalization import normalize_email, normalize_phone


STANDARD_CONTACT_PROPERTIES = {
    "email", "firstname", "lastname", "jobtitle", "phone", "company",
}


class HubSpotContacts:
    def __init__(self, client: HubSpotClient) -> None:
        self.client = client
        self._properties: set[str] | None = None
        self._search_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._record_cache: dict[str, dict[str, Any]] = {}

    def available_properties(self) -> set[str]:
        if self._properties is not None:
            return set(self._properties)
        properties = set(STANDARD_CONTACT_PROPERTIES)
        try:
            payload = self.client.request("GET", f"/crm/properties/{API_VERSION}/contacts")
        except HubSpotError as exc:
            if exc.category != "missing_permissions":
                raise
        else:
            properties.update(
                str(item.get("name", "")) for item in payload.get("results", [])
                if item.get("name")
            )
        self._properties = properties
        return set(properties)

    def _property_list(self) -> str:
        return ",".join(sorted(self.available_properties()))

    def get(self, record_id: str) -> dict[str, Any]:
        if record_id in self._record_cache:
            return self._record_cache[record_id]
        record = self.client.request(
            "GET", f"/crm/objects/{API_VERSION}/contacts/{record_id}",
            params={"properties": self._property_list()},
        )
        self._record_cache[record_id] = record
        return record

    def get_fresh(self, record_id: str) -> dict[str, Any]:
        record = self.client.request(
            "GET", f"/crm/objects/{API_VERSION}/contacts/{record_id}",
            params={"properties": self._property_list()},
        )
        self._record_cache[record_id] = record
        return record

    def preload(self, record_ids: list[str]) -> None:
        missing = [record_id for record_id in record_ids if record_id not in self._record_cache]
        if missing:
            self._record_cache.update(batch_read(
                self.client, "contacts", missing, sorted(self.available_properties()),
            ))

    def search(self, property_name: str, value: str) -> list[dict[str, Any]]:
        key = (property_name, value.casefold())
        if key not in self._search_cache:
            payload = self.client.request(
                "POST", f"/crm/objects/{API_VERSION}/contacts/search", search=True,
                json={
                    "filterGroups": [{"filters": [{
                        "propertyName": property_name, "operator": "EQ", "value": value,
                    }]}],
                    "properties": sorted(self.available_properties()),
                    "limit": 10,
                },
            )
            self._search_cache[key] = list(payload.get("results", []))
        return list(self._search_cache[key])

    def candidates(self, local: dict[str, Any], linked_id: str = "") -> list[dict[str, Any]]:
        if linked_id:
            try:
                return [self.get(linked_id)]
            except HubSpotError as exc:
                if exc.category != "not_found":
                    raise
        records: dict[str, dict[str, Any]] = {}
        available = self.available_properties()
        local_id = str(local.get("id", ""))
        if local_id and "leadharbor_contact_id" in available:
            for record in self.search("leadharbor_contact_id", local_id):
                records[str(record.get("id", ""))] = record
        email = normalize_email(str(local.get("email", "")))
        if email:
            for record in self.search("email", email):
                records[str(record.get("id", ""))] = record
        phone = normalize_phone(str(local.get("phone", "")))
        if phone:
            for record in self.search("phone", str(local.get("phone", "")).strip()):
                records[str(record.get("id", ""))] = record
        first = str(local.get("contact_first_name", "")).strip()
        last = str(local.get("contact_last_name", "")).strip()
        if first and last:
            for record in self.search("firstname", first):
                props = record.get("properties", {})
                if str(props.get("lastname", "")).strip().casefold() == last.casefold():
                    records[str(record.get("id", ""))] = record
        return list(records.values())

    def create(self, properties: dict[str, str]) -> dict[str, Any]:
        return self.client.request(
            "POST", f"/crm/objects/{API_VERSION}/contacts", json={"properties": properties},
        )

    def update(self, record_id: str, properties: dict[str, str]) -> dict[str, Any]:
        record = self.client.request(
            "PATCH", f"/crm/objects/{API_VERSION}/contacts/{record_id}",
            json={"properties": properties},
        )
        self._record_cache[record_id] = record
        return record

    def create_many(self, items: list[tuple[str, dict[str, str]]]) -> dict[str, dict[str, Any]]:
        return batch_create(self.client, "contacts", items)

    def update_many(
        self, items: list[tuple[str, str, dict[str, str]]],
    ) -> dict[str, dict[str, Any]]:
        return batch_update(self.client, "contacts", items)
