from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import requests


LOG = logging.getLogger("leadharbor.hubspot")
API_VERSION = "2026-03"
DEFAULT_BASE_URL = "https://api.hubapi.com"


@dataclass
class HubSpotError(RuntimeError):
    category: str
    message: str
    status_code: int | None = None
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


def chunked(values: Iterable[Any], size: int = 100) -> Iterable[list[Any]]:
    if size < 1 or size > 100:
        raise ValueError("HubSpot batch size must be between 1 and 100")
    batch: list[Any] = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


class HubSpotClient:
    def __init__(
        self,
        access_token: str,
        *,
        session: Any | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: tuple[float, float] = (5.0, 20.0),
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        search_requests_per_second: float = 4.0,
    ) -> None:
        token = (access_token or "").strip()
        if not token:
            raise HubSpotError("missing_credentials", "HubSpot credential is not configured.")
        self._token = token
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.sleep = sleep
        self.monotonic = monotonic
        self._search_interval = 1.0 / max(0.1, search_requests_per_second)
        self._last_search_at = 0.0
        self._search_lock = threading.Lock()

    def _wait_for_search_slot(self) -> None:
        with self._search_lock:
            now = self.monotonic()
            delay = self._search_interval - (now - self._last_search_at)
            if self._last_search_at and delay > 0:
                self.sleep(delay)
                now = self.monotonic()
            self._last_search_at = now

    @staticmethod
    def _message_for(status: int, payload: Any) -> tuple[str, str, bool]:
        detail = payload.get("message", "") if isinstance(payload, dict) else ""
        if status == 400:
            return "validation", "HubSpot rejected a field value as invalid.", False
        if status == 401:
            return "invalid_credentials", "HubSpot credentials are invalid.", False
        if status == 403:
            return "missing_permissions", "HubSpot permissions are missing.", False
        if status == 404:
            return "not_found", "The HubSpot record or property was not found.", False
        if status == 409:
            return "conflict", "HubSpot reported a record conflict.", False
        if status == 429:
            return "rate_limited", "HubSpot is temporarily rate limited.", True
        if status >= 500:
            return "service_unavailable", "HubSpot is temporarily unavailable.", True
        safe_detail = detail[:200] if detail else "HubSpot request failed."
        return "api_error", safe_detail, False

    def request(self, method: str, path: str, *, search: bool = False, **kwargs: Any) -> dict[str, Any]:
        if search:
            self._wait_for_search_slot()
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update({
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.request(
                    method.upper(), url, headers=headers, timeout=self.timeout, **kwargs,
                )
            except requests.RequestException as exc:
                if attempt < self.max_attempts:
                    delay = min(8.0, float(2 ** (attempt - 1)))
                    LOG.warning("HubSpot network failure; retry %s/%s", attempt, self.max_attempts)
                    self.sleep(delay)
                    continue
                raise HubSpotError(
                    "network", "HubSpot is unreachable. Check the network connection.",
                    retryable=True,
                ) from exc
            try:
                payload = response.json()
            except (TypeError, ValueError):
                payload = {}
            if 200 <= response.status_code < 300:
                return payload if isinstance(payload, dict) else {}
            category, message, retryable = self._message_for(response.status_code, payload)
            if retryable and attempt < self.max_attempts:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = max(0.0, float(retry_after)) if retry_after else min(
                        8.0, float(2 ** (attempt - 1)),
                    )
                except ValueError:
                    delay = min(8.0, float(2 ** (attempt - 1)))
                LOG.warning(
                    "HubSpot request returned HTTP %s; retry %s/%s",
                    response.status_code, attempt, self.max_attempts,
                )
                self.sleep(delay)
                continue
            raise HubSpotError(category, message, response.status_code, retryable)
        raise HubSpotError("api_error", "HubSpot request failed.")

    def test_connection(self) -> dict[str, Any]:
        return self.request(
            "GET", f"/crm/objects/{API_VERSION}/companies",
            params={"limit": 1, "properties": "name"},
        )
