---
name: sync-test-scenarios
description: How to build and use FakeSalesforce, the test double that stands in for Salesforce (build-plan phase 4). No separate app or container — the fake lives in the test suite (user decision). Use when writing tests or code that talks to Salesforce, or when building the webhook/outbound/catch-up code that these scenarios exercise.
---

# FakeSalesforce: testing without a real CRM

The brief: *"call a fake CRM you write yourself."* **User decision: this is test code, not a
Django app** — no `fake_salesforce` app, no extra container. Build it in `src/sync/tests/`.
Design reference: `docs/design.md` §6 (the record API, upsert by external ID, the "changed
since" query) and the outcome table in the `sync-engine` skill.

## 1. `src/sync/tests/fakes.py` — `FakeSalesforce` (4.1.1)

In-memory store, keyed by Salesforce ID and by external ID:

```python
class FakeSalesforce:
    def __init__(self):
        self.records = {}        # salesforce_id -> {"external_id": ..., "fields": {...}, "modified_at": ..., "modified_by": ...}
        self.calls = []          # (method, path, body, timestamp) — the call log, see 4.2.2
        self.mode = "up"         # see failure modes, 4.2.1

    def upsert_by_external_id(self, external_id, fields, actor="api"):
        """What the real record API's upsert endpoint does: match on external_id, create if
        no match. Returns the salesforce_id, new or existing."""
        ...

    def update(self, salesforce_id, fields, actor="api"): ...

    def changed_since(self, since, page_size=2000): ...   # Q3's query, 2,000/page (design §10)
```

- Keep this **separate from Django's test database** — it's a plain Python object, not a
  model, so tests can create a fresh one per test with no DB migration overhead.
- `upsert_by_external_id` called twice with the same `external_id` must return the **same**
  `salesforce_id` and must not create a second record — this is what makes Q13 (safe retry of a
  timed-out create) actually testable.

## 2. Plugging into the real client (4.1.2)

`SalesforceClient` (the `sync-engine` skill, 5.1) talks `httpx`. Point it at the fake with
`httpx.MockTransport`, built into httpx — no new dependency:

```python
def transport_for(fake: FakeSalesforce) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        fake.calls.append((request.method, str(request.url), request.content, timezone.now()))
        if fake.mode == "down":
            raise httpx.ConnectError("fake: down")
        if fake.mode == "slow":
            time.sleep(fake.slow_delay)
        if fake.mode == "throttle":
            return httpx.Response(429, headers={"Retry-After": "1"})
        if fake.mode == "error":
            return httpx.Response(500)
        if fake.mode == "reject" and _record_id_from(request) == fake.reject_id:
            return httpx.Response(400, json={"error": "bad data"})
        if fake.mode == "auth":
            return httpx.Response(401)
        return _route_to_fake_method(fake, request)   # dispatch by method+path to upsert/update/changed_since
    return httpx.MockTransport(handler)

client = httpx.Client(transport=transport_for(fake), base_url=settings.SALESFORCE_BASE_URL)
```

This runs the **real** `SalesforceClient` code end to end with no network — the thing under
test is our retry/backoff/outcome-mapping logic, not the fake.

## 3. The local-server fixture (4.1.3) — only for the restart test

One test (5.5, the brief's restart-while-down check) needs Salesforce to be reachable from a
**separate OS process** (the outbound task restarting), which `MockTransport` can't do — it
only exists inside the test process. For that one test, serve the same `FakeSalesforce` over
real HTTP:

```python
import http.server, threading

def serve(fake: FakeSalesforce, port=0) -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = _make_handler(fake)   # http.server.BaseHTTPRequestHandler subclass, same dispatch as the transport
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]
```

Stdlib only (`http.server`, `threading`) — no extra dependency. Use a pytest fixture that
starts this, yields the port, and calls `server.shutdown()` on teardown. Point
`SALESFORCE_BASE_URL` (via `override_settings` or an env var passed to the subprocess) at
`http://127.0.0.1:<port>`.

## 4. Failure modes (4.2.1) and the call log (4.2.2)

`fake.mode` is one of `up | down | slow | throttle | error | reject | auth`; tests flip it
mid-test to simulate an outage starting or ending. `fake.calls` is a plain list tests can
assert against — e.g. `len(fake.calls) == 0` to prove a webhook never triggered an outbound
call (the sync-loop check), or diff timestamps in `fake.calls` to measure the request rate
(8.1.3 in `sync-testing`).

## 5. Scenario builders (4.3) — `src/sync/tests/scenarios.py`

Thin helpers that build **webhook payloads**, matching the `WebhookEvent` schema in
`docs/openapi.yml`. Q2 (revised 2026-11-27): the default assumption is **changed fields only**,
matching the brief's own sample — `sales_edit()` builds that shape. `sales_edit_whole_record()`
builds the fallback case, for the one test that needs to prove the design holds up even if a
webhook ever carries the whole record instead.

```python
def sales_edit(fake: FakeSalesforce, salesforce_id: str, **changed_fields) -> dict:
    """Changes a record in the fake and returns the matching webhook payload — only the
    fields actually changed (Q2's default assumption), as the brief's own sample does."""
    record = fake.records[salesforce_id]
    record["fields"].update(changed_fields)
    record["modified_at"] = timezone.now()
    record["modified_by"] = "sales.user@client.example"
    return {
        "eventId": f"evt_{uuid4()}", "recordId": salesforce_id, "objectType": "Contact",
        "occurredAt": record["modified_at"].isoformat(), "changedBy": record["modified_by"],
        "changedFields": dict(changed_fields),   # only what changed, not the whole record
    }

def sales_edit_whole_record(fake: FakeSalesforce, salesforce_id: str, **changed_fields) -> dict:
    """Same as sales_edit(), but the payload carries every field on the record (Q2's "other
    way") — use this in the one test that proves apply()'s sf_value comparison makes that case
    safe too, not in every test."""
    record = fake.records[salesforce_id]
    record["fields"].update(changed_fields)
    record["modified_at"] = timezone.now()
    record["modified_by"] = "sales.user@client.example"
    return {
        "eventId": f"evt_{uuid4()}", "recordId": salesforce_id, "objectType": "Contact",
        "occurredAt": record["modified_at"].isoformat(), "changedBy": record["modified_by"],
        "changedFields": dict(record["fields"]),   # every field, including unchanged ones
    }

def our_echo(fake, salesforce_id, **fields) -> dict:
    """What a webhook looks like when IT'S OUR OWN update coming back — changedBy is the
    integration user identifier (see sync-engine's INTEGRATION_USER_IDENTIFIER). Only the
    fields we sent, matching sales_edit()'s shape."""
    ...  # same shape, changedBy = settings.SALESFORCE_INTEGRATION_USER

def duplicate(payload: dict) -> dict:
    return dict(payload)   # same eventId — post it twice

def stale_after(newer_payload: dict, older_occurred_at: datetime, **older_fields) -> dict:
    """An older event for the same record, delivered AFTER a newer one already applied. Carries
    only the given (now-stale) field values — it does not need or reuse newer_payload's
    changedFields, since each webhook only reports its own change."""
    return {
        "eventId": f"evt_{uuid4()}", "recordId": newer_payload["recordId"],
        "objectType": newer_payload["objectType"], "occurredAt": older_occurred_at.isoformat(),
        "changedBy": newer_payload["changedBy"], "changedFields": dict(older_fields),
    }
```

## 6. The `brief` pytest marker (4.3.3)

Register it in `pyproject.toml`'s `[tool.pytest.ini_options]`:

```toml
markers = ["brief: the brief's four required checks (duplicate, out-of-order, no-loop, restart-while-down)"]
```

Tag exactly the tests that cover the brief's four checks with `@pytest.mark.brief` as each is
built (phases 3, 2, 3, 5). `scripts/demo.sh` (phase 8) runs `pytest -m brief -v` as its core —
see `sync-testing`.

## How this plugs into each phase

- **Phase 3 (webhook):** `sales_edit()` / `duplicate()` / `stale_after()` posted to the webhook
  endpoint, asserting on the DB and on `FakeSalesforce.calls` staying empty (no echo).
- **Phase 5 (outbound):** a portal edit, then run `send_outbound()` against the
  `MockTransport`-backed client, asserting `fake.calls` got the right request and the record's
  `salesforce_id` got filled in on a create.
- **Phase 6 (catch-up):** mutate `fake.records` directly (simulating a sales edit with no
  webhook sent), run `run_catchup()`, assert it shows up via `apply()`.
