from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import httpx
from django.conf import settings


@dataclass(frozen=True)
class ClientOutcome:
    outcome: str  # "ok" | "unreachable" | "throttled" | "rejected" | "auth_error"
    salesforce_id: Optional[str] = None
    retry_after: int = 1
    error_message: str = ""


class SalesforceClient:
    """HTTP client talking to Salesforce record API (design §6, build-plan 5.1)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = (base_url or getattr(settings, "SALESFORCE_BASE_URL", "https://api.salesforce.com")).rstrip("/")
        self.token = token or getattr(settings, "SALESFORCE_TOKEN", "")
        self.transport = transport
        self._client = httpx.Client(
            base_url=self.base_url,
            transport=self.transport,
            timeout=10.0,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def upsert(self, object_type: str, external_id: str, fields: dict[str, Any]) -> ClientOutcome:
        """Upsert record by external ID (Q13): PATCH .../sobjects/{ObjectType}/external_id/{external_id}."""
        url = f"/services/data/v60.0/sobjects/{object_type}/external_id/{external_id}"
        return self._send("PATCH", url, json=fields)

    def update(self, object_type: str, salesforce_id: str, fields: dict[str, Any]) -> ClientOutcome:
        """Update record by Salesforce ID: PATCH .../sobjects/{ObjectType}/{salesforce_id}."""
        url = f"/services/data/v60.0/sobjects/{object_type}/{salesforce_id}"
        return self._send("PATCH", url, json=fields)

    def query(self, soql: str) -> tuple[ClientOutcome, dict[str, Any]]:
        """Query records via SOQL: GET /services/data/v60.0/query/?q=..."""
        url = f"/services/data/v60.0/query/?q={quote(soql)}"
        outcome, data = self._send_query("GET", url)
        return outcome, data

    def _send(self, method: str, url: str, **kwargs) -> ClientOutcome:
        try:
            resp = self._client.request(method, url, headers=self._headers(), **kwargs)
            return self._map_response(resp)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            return ClientOutcome(outcome="unreachable", error_message=str(exc))
        except Exception as exc:
            return ClientOutcome(outcome="unreachable", error_message=f"Unexpected: {exc}")

    def _send_query(self, method: str, url: str, **kwargs) -> tuple[ClientOutcome, dict[str, Any]]:
        try:
            resp = self._client.request(method, url, headers=self._headers(), **kwargs)
            outcome = self._map_response(resp)
            data = resp.json() if resp.status_code == 200 else {}
            return outcome, data
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            return ClientOutcome(outcome="unreachable", error_message=str(exc)), {}
        except Exception as exc:
            return ClientOutcome(outcome="unreachable", error_message=f"Unexpected: {exc}"), {}

    def _map_response(self, response: httpx.Response) -> ClientOutcome:
        code = response.status_code
        if code in (200, 201, 204):
            sf_id = None
            if code != 204:
                try:
                    sf_id = response.json().get("id")
                except Exception:
                    pass
            return ClientOutcome(outcome="ok", salesforce_id=sf_id)

        if code == 429:
            try:
                retry_after = int(response.headers.get("Retry-After", 1))
            except (ValueError, TypeError):
                retry_after = 1
            return ClientOutcome(outcome="throttled", retry_after=max(1, retry_after))

        if code in (401, 403):
            return ClientOutcome(outcome="auth_error", error_message=response.text)

        if code == 400:
            return ClientOutcome(outcome="rejected", error_message=response.text)

        if code >= 500:
            return ClientOutcome(outcome="unreachable", error_message=f"HTTP {code}: {response.text}")

        # Any other 4xx:
        return ClientOutcome(outcome="rejected", error_message=f"HTTP {code}: {response.text}")
