import json
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import httpx
from django.utils import timezone


class FakeSalesforce:
    """In-memory test double for Salesforce (Phase 4, design §6, build-plan 4.1-4.2).

    Simulates the official record API (upsert by external ID, update by ID)
    and query API (changed-since query), with controllable failure modes and call logging.
    """

    def __init__(self):
        # salesforce_id -> {"external_id": str, "object_type": str, "fields": dict, "modified_at": datetime, "modified_by": str}
        self.records: dict[str, dict[str, Any]] = {}
        # (method, url, body_bytes, timestamp)
        self.calls: list[tuple[str, str, bytes, datetime]] = []
        # up | down | slow | throttle | error | reject | auth
        self.mode: str = "up"
        self.slow_delay: float = 0.05
        self.reject_id: Optional[str] = None

    def upsert_by_external_id(
        self,
        external_id: str,
        fields: dict[str, Any],
        object_type: str = "Contact",
        actor: str = "sales.user@client.example",
    ) -> tuple[str, bool]:
        """Idempotent upsert matching on external_id (Q13).

        Returns (salesforce_id, created).
        """
        ext_str = str(external_id)
        now = timezone.now()
        for sf_id, rec in self.records.items():
            if rec.get("external_id") == ext_str and rec.get("object_type") == object_type:
                rec["fields"].update(fields)
                rec["modified_at"] = now
                rec["modified_by"] = actor
                return sf_id, False

        prefix = "001" if object_type == "Customer" else "003"
        sf_id = f"{prefix}{uuid4().hex[:15]}"
        self.records[sf_id] = {
            "salesforce_id": sf_id,
            "external_id": ext_str,
            "object_type": object_type,
            "fields": dict(fields),
            "modified_at": now,
            "modified_by": actor,
        }
        return sf_id, True

    def update(
        self,
        salesforce_id: str,
        fields: dict[str, Any],
        actor: str = "sales.user@client.example",
    ) -> str:
        """Update existing record by salesforce_id."""
        if salesforce_id not in self.records:
            raise KeyError(f"Record {salesforce_id!r} not found in FakeSalesforce")
        rec = self.records[salesforce_id]
        rec["fields"].update(fields)
        rec["modified_at"] = timezone.now()
        rec["modified_by"] = actor
        return salesforce_id

    def changed_since(
        self,
        since: datetime,
        page_size: int = 2000,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], bool, int]:
        """Query records modified since timestamp T (Q3, design §10).

        Returns (records_page, done, next_offset).
        """
        matching = [
            rec for rec in self.records.values()
            if rec["modified_at"] >= since
        ]
        matching.sort(key=lambda r: r["modified_at"])
        page = matching[offset : offset + page_size]
        next_offset = offset + len(page)
        done = next_offset >= len(matching)
        return page, done, next_offset


def _handle_request(fake: FakeSalesforce, method: str, url_str: str, body_bytes: bytes) -> tuple[int, dict[str, str], bytes]:
    """Shared HTTP dispatch for both MockTransport and ThreadingHTTPServer."""
    fake.calls.append((method, url_str, body_bytes, timezone.now()))

    if fake.mode == "down":
        return 503, {"content-type": "application/json"}, b'{"error": "connection refused"}'
    if fake.mode == "slow":
        time.sleep(fake.slow_delay)
    if fake.mode == "throttle":
        return 429, {"retry-after": "1", "content-type": "application/json"}, b'{"message": "Too Many Requests"}'
    if fake.mode == "error":
        return 500, {"content-type": "application/json"}, b'{"error": "internal server error"}'
    if fake.mode == "auth":
        return 401, {"content-type": "application/json"}, b'[{"message": "Session expired or invalid", "errorCode": "INVALID_SESSION_ID"}]'

    parsed = urlparse(url_str)
    path = parsed.path
    body_data = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}

    # Upsert by external ID: PATCH/PUT .../sobjects/{ObjectType}/external_id/{external_id}
    m_upsert = re.search(r"/sobjects/(\w+)/external_id/([^/?]+)", path)
    if m_upsert and method in ("PATCH", "PUT"):
        obj_type, ext_id = m_upsert.group(1), unquote(m_upsert.group(2))
        if fake.mode == "reject" and (fake.reject_id is None or fake.reject_id == ext_id):
            return 400, {"content-type": "application/json"}, b'[{"message": "Bad request", "errorCode": "BAD_REQUEST"}]'
        sf_id, created = fake.upsert_by_external_id(ext_id, body_data, object_type=obj_type)
        status_code = 201 if created else 200
        resp_data = {"id": sf_id, "success": True, "created": created}
        return status_code, {"content-type": "application/json"}, json.dumps(resp_data).encode("utf-8")

    # Update by ID: PATCH .../sobjects/{ObjectType}/{salesforce_id}
    m_update = re.search(r"/sobjects/(\w+)/([^/?]+)", path)
    if m_update and method == "PATCH":
        obj_type, sf_id = m_update.group(1), unquote(m_update.group(2))
        if fake.mode == "reject" and (fake.reject_id is None or fake.reject_id == sf_id):
            return 400, {"content-type": "application/json"}, b'[{"message": "Bad request", "errorCode": "BAD_REQUEST"}]'
        try:
            fake.update(sf_id, body_data)
            return 204, {}, b""
        except KeyError:
            return 404, {"content-type": "application/json"}, b'[{"message": "Record not found", "errorCode": "NOT_FOUND"}]'

    # Query API: GET .../query/?q=...
    if path.endswith("/query/") or "/query" in path:
        qs = parse_qs(parsed.query)
        q_str = qs.get("q", [""])[0]
        since = timezone.now() - timezone.timedelta(days=365)
        m_since = re.search(r"SystemModstamp\s*>=\s*(\S+)", q_str)
        if m_since:
            try:
                since = datetime.fromisoformat(m_since.group(1).strip("'\""))
                if timezone.is_naive(since):
                    since = timezone.make_aware(since)
            except ValueError:
                pass

        offset = int(qs.get("offset", ["0"])[0])
        records, done, next_offset = fake.changed_since(since, offset=offset)
        out_records = []
        for r in records:
            rec_dict = {
                "Id": r["salesforce_id"],
                "SystemModstamp": r["modified_at"].isoformat(),
                "attributes": {"type": r["object_type"]},
                **r["fields"],
            }
            if "modified_by" in r:
                rec_dict["modified_by"] = r["modified_by"]
            out_records.append(rec_dict)
        resp = {
            "totalSize": len(out_records),
            "done": done,
            "records": out_records,
        }
        if not done:
            resp["nextRecordsUrl"] = f"/services/data/v60.0/query/?q={quote(q_str)}&offset={next_offset}"
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode("utf-8")

    return 404, {"content-type": "application/json"}, b'{"error": "route not found"}'


def transport_for(fake: FakeSalesforce) -> httpx.MockTransport:
    """Plug into httpx.Client(transport=transport_for(fake)) for network-free testing (4.1.2)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if fake.mode == "down":
            fake.calls.append((request.method, str(request.url), request.content, timezone.now()))
            raise httpx.ConnectError("fake: down")
        status_code, headers, body = _handle_request(
            fake, request.method, str(request.url), request.content
        )
        return httpx.Response(status_code, headers=headers, content=body)

    return httpx.MockTransport(handler)


class _FakeSalesforceHTTPHandler(BaseHTTPRequestHandler):
    fake: FakeSalesforce

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def _dispatch(self, method: str):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        status_code, headers, resp_body = _handle_request(self.fake, method, self.path, body)
        self.send_response(status_code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp_body)

    def log_message(self, format, *args):
        pass  # Quiet test output


def serve_fake_salesforce(fake: FakeSalesforce, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Serve FakeSalesforce over localhost HTTP for multi-process worker tests (4.1.3).

    Returns (server, base_url).
    """
    handler_cls = type("Handler", (_FakeSalesforceHTTPHandler,), {"fake": fake})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{actual_port}"
