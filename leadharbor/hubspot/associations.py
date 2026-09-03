from __future__ import annotations

from typing import Any

from .client import HubSpotClient, HubSpotError, chunked


class HubSpotAssociations:
    def __init__(self, client: HubSpotClient) -> None:
        self.client = client

    def associate_contact_company(self, contact_id: str, company_id: str) -> None:
        self.client.request(
            "PUT",
            f"/crm/v4/objects/contact/{contact_id}/associations/default/company/{company_id}",
        )

    def associate_many(
        self, items: list[tuple[str, str, str]],
    ) -> dict[str, dict[str, Any]]:
        outcomes: dict[str, dict[str, Any]] = {}
        for group in chunked(items, 100):
            try:
                self.client.request(
                    "POST", "/crm/v4/associations/contact/company/batch/associate/default",
                    json={"inputs": [
                        {"from": {"id": contact_id}, "to": {"id": company_id}}
                        for _, contact_id, company_id in group
                    ]},
                )
            except HubSpotError as exc:
                outcomes.update({key: {"error": str(exc)} for key, _, _ in group})
            else:
                outcomes.update({key: {"ok": True} for key, _, _ in group})
        return outcomes
