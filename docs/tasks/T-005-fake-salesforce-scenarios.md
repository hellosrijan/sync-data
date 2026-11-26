# T-005: `FakeSalesforce` Test Double and Scenario Builders

**Status:** done
**Owner:** agent-monica
**Requested:** 2026-11-27 by user: "Phase 4: Test scenarios (a fake Salesforce inside the tests) — FakeSalesforce in-process double with failure modes, MockTransport and ThreadingHTTPServer fixtures, and scenario builders (sales_edit, our_echo, duplicate, stale_after)."

**Scope:** `docs/build-plan.md` Phase 4 (tasks 4.1, 4.2, 4.3), `.claude/skills/sync-test-scenarios/SKILL.md`.
Out of scope: Outbound worker/limiter (Phase 5), catch-up task (Phase 6), portal API views (Phase 7).

## Acceptance criteria

Files:
- `src/sync/tests/fakes.py` (`FakeSalesforce`, `transport_for`, `serve_fake_salesforce` fixture/helper)
- `src/sync/tests/scenarios.py` (`sales_edit`, `sales_edit_whole_record`, `our_echo`, `duplicate`, `stale_after`)
- `src/sync/tests/test_fakes.py` (comprehensive tests for fake CRM and scenarios)

- **AC-4.1.1** (build-plan 4.1.1; design §6, Q3, Q13; skill §1)
  `FakeSalesforce` stores in-memory records keyed by `salesforce_id` and tracks `external_id`:
  - `upsert_by_external_id(external_id, fields, object_type="Contact", actor="api")`: if `external_id` matches existing record, updates fields and returns existing `salesforce_id`. If no match, generates new Salesforce ID (e.g. `003...` for Contact, `001...` for Customer), stores record, and returns it. Calling twice with same `external_id` is idempotent (returns same ID, leaves 1 record).
  - `update(salesforce_id, fields, actor="api")`: updates fields on existing record, advances `modified_at` and `modified_by`. Raises KeyError/LookupError if record unknown.
  - `changed_since(since, page_size=2000)`: returns records with `modified_at >= since` (or strictly `>`), paged by `page_size`. If more records than `page_size`, returns `records` plus a pagination token / next cursor (or next page).
- **AC-4.1.2** (build-plan 4.1.2; skill §2)
  `transport_for(fake)` returns an `httpx.MockTransport` that handles the REST requests a real client makes:
  - `PATCH /services/data/v60.0/sobjects/{ObjectType}/external_id/{external_id}` → calls `upsert_by_external_id`.
  - `PATCH /services/data/v60.0/sobjects/{ObjectType}/{salesforce_id}` → calls `update`.
  - `GET /services/data/v60.0/query/?q=...` → parses `SystemModstamp >= ...` and calls `changed_since`.
  Calling these routes via an `httpx.Client(transport=transport_for(fake))` executes without real network sockets.
- **AC-4.1.3** (build-plan 4.1.3; skill §3)
  `serve_fake_salesforce(fake, port=0)` provides a live HTTP server (stdlib `http.server.ThreadingHTTPServer`) backed by the same `FakeSalesforce` instance. A real HTTP client can issue requests against `http://127.0.0.1:<port>` and receive the exact same responses as `MockTransport`.
- **AC-4.2.1** (build-plan 4.2.1; skill §4)
  `FakeSalesforce` supports dynamic failure modes via `fake.mode`:
  - `"up"`: normal 200/201 responses.
  - `"down"`: `MockTransport` raises `httpx.ConnectError("fake: down")`; HTTP server returns 503 or terminates connection.
  - `"slow"`: delays response by `fake.slow_delay` (e.g. 0.05s).
  - `"throttle"`: returns HTTP 429 with `Retry-After: 1`.
  - `"error"`: returns HTTP 500 Internal Server Error.
  - `"reject"`: returns HTTP 400 Bad Request with error payload when matching `fake.reject_id`.
  - `"auth"`: returns HTTP 401 Unauthorized.
- **AC-4.2.2** (build-plan 4.2.2; skill §4)
  `FakeSalesforce.calls` logs every incoming request as `(method, url, body_bytes, timestamp)` with an accurate timestamp. Tests can inspect `fake.calls` to verify call counts, URLs, payloads, and calculate request rates.
- **AC-4.3.1** (build-plan 4.3.1; openapi.yml; skill §5)
  `scenarios.py` provides helpers:
  - `sales_edit(fake, salesforce_id, **changed_fields)`: updates record in fake, returns webhook payload with `changedFields` containing ONLY the changed fields (Q2 revised).
  - `sales_edit_whole_record(fake, salesforce_id, **changed_fields)`: updates record in fake, returns webhook payload with `changedFields` containing ALL fields on the record (Q2 fallback).
  - `our_echo(fake, salesforce_id, **fields)`: returns webhook payload with `changedBy = settings.SALESFORCE_INTEGRATION_USER`.
  - Posting the payload from `sales_edit` to `/api/v1/webhooks/salesforce/` succeeds with HTTP 200 and applies the change cleanly.
- **AC-4.3.2** (build-plan 4.3.2; skill §5)
  `duplicate(payload)` returns identical copy with same `eventId`. `stale_after(newer_payload, older_time, **older_fields)` returns a valid webhook payload with older `occurredAt` and distinct `eventId`.
- **AC-4.3.3** (build-plan 4.3.3; pyproject.toml)
  The `brief` pytest marker is registered in `pyproject.toml` and applied to tests exercising the brief's required checks.
- **AC-X** (cross-cutting)
  `manage.py check` passes with 0 issues; all tests pass; no real network calls; no secrets leaked.

## Routing plan

| Step | Agent | Input | Done when |
|---|---|---|---|
| 1 | agent-phoebe | AC-4.1.1 to 4.3.3 | `src/sync/tests/fakes.py` and `src/sync/tests/scenarios.py` implemented |
| 2 | agent-ross | Phoebe's files | `src/sync/tests/test_fakes.py` written, full suite passes with AC coverage |
| 3 | agent-chandler | Ross's results | Docs, build-plan checkboxes, and memory updated |
| 4 | agent-monica | All artifacts | Independent verification, verdict recorded |

## Log

### 2026-11-27 agent-monica: task initialized and routed
- Created task ledger `T-005-fake-salesforce-scenarios.md`.
- Formulated criteria AC-4.1.1 to AC-4.3.3 based on `.claude/skills/sync-test-scenarios/SKILL.md` and `docs/build-plan.md`.
- Setting status to `coding`. Dispatching agent-phoebe.

### 2026-11-27 agent-phoebe: implementation complete
- **Did:**
  - Implemented `FakeSalesforce`, `transport_for`, and `serve_fake_salesforce` in `src/sync/tests/fakes.py`:
    - In-memory record store tracking `salesforce_id`, `external_id`, `fields`, `modified_at`, `modified_by`.
    - Idempotent `upsert_by_external_id` (returns stable ID, `created` flag, 001/003 prefixes).
    - `update` by salesforce ID with KeyError on missing record.
    - `changed_since` with paging.
    - Full failure modes: `up`, `down`, `slow`, `throttle` (429), `error` (500), `reject` (400), `auth` (401).
    - `calls` log recording method, url, body, and timestamp.
    - `transport_for` `httpx.MockTransport` handler.
    - `serve_fake_salesforce` local `ThreadingHTTPServer` on localhost.
  - Implemented `scenarios.py` in `src/sync/tests/scenarios.py`:
    - `sales_edit` (changed fields only, Q2 revised).
    - `sales_edit_whole_record` (all fields, Q2 fallback).
    - `our_echo` (changedBy = `settings.SALESFORCE_INTEGRATION_USER`).
    - `duplicate` and `stale_after`.
- **Files:**
  - `src/sync/tests/fakes.py`
  - `src/sync/tests/scenarios.py`
- **Ran:** `manage.py check` (0 issues), imports clean.

### 2026-11-27 agent-ross: tests written and passed
- **Did:**
  - Implemented `src/sync/tests/test_fakes.py` (11 tests covering all AC-4.1.1 to AC-4.3.3):
    - Upsert idempotency (Contact and Customer prefixes).
    - Update by ID and KeyError on missing record.
    - Changed-since query paging across multiple pages.
    - MockTransport upsert create/update, update by ID, query API.
    - Live `ThreadingHTTPServer` over TCP port matching MockTransport.
    - All failure modes (`down`, `slow`, `throttle`, `error`, `reject`, `auth`).
    - Call log inspection with timestamps.
    - Scenario helpers (`sales_edit`, `sales_edit_whole_record`, `our_echo`, `duplicate`, `stale_after`).
    - End-to-end integration: `sales_edit` payload posted to `/api/v1/webhooks/salesforce/` updates portal database (marked `@pytest.mark.brief`).
- **Files:** `src/sync/tests/test_fakes.py`
- **Ran:** `uv run pytest -q` -> 171 passed in 8.21s; `pytest -m brief` -> 9 passed.

### 2026-11-27 agent-chandler: docs & status updated
- **Did:**
  - Updated `docs/build-plan.md`: subtasks 4.1.1 through 4.3.3 marked `*Done 2026-11-27.*`.
  - Updated Status table: Phase 4 marked `✅ Done`.
  - Stored memory lesson: `[team] T-005 FakeSalesforce test double and scenario builders in test suite`.
- **Ran:** `manage.py check`, `pytest -q` (171 passed), `bash scripts/build-wiki.sh` (7 pages).

## Test results

`uv run pytest -q` → **171 passed in 8.21s** (160 existing + 11 new in `test_fakes.py`).
`pytest -m brief` → **9 passed**.
`manage.py check` → clean (0 issues).
`makemigrations --check --dry-run` → clean.

| AC | Test(s) in `src/sync/tests/test_fakes.py` | Result |
|---|---|---|
| AC-4.1.1 | `test_ac_4_1_1_upsert_by_external_id_is_idempotent`, `_customer_upsert_uses_001_prefix`, `_update_by_salesforce_id`, `_changed_since_query_paging` | pass |
| AC-4.1.2 | `test_ac_4_1_2_mock_transport_upsert_and_update`, `_mock_transport_query` | pass |
| AC-4.1.3 | `test_ac_4_1_3_live_http_server_matches_transport` | pass |
| AC-4.2.1 | `test_ac_4_2_1_failure_modes` (covers down, slow, throttle 429, error 500, reject 400, auth 401) | pass |
| AC-4.2.2 | `test_ac_4_2_2_call_log_records_timestamps_and_methods` | pass |
| AC-4.3.1–4.3.2 | `test_ac_4_3_scenario_builders_helpers` | pass |
| AC-4.3.3 / Done when | `test_ac_4_3_scenario_posts_to_webhook_and_appears_in_portal` (marked `@pytest.mark.brief`) | pass |
| AC-X | `manage.py check`, all 171 tests pass, no network calls in unit tests, clean migrations | pass |

## Docs updated
- `docs/build-plan.md`: subtasks 4.1.1 through 4.3.3 marked done; Status table updated.
- `src/sync/tests/fakes.py`: `FakeSalesforce`, `transport_for`, `serve_fake_salesforce`.
- `src/sync/tests/scenarios.py`: `sales_edit`, `sales_edit_whole_record`, `our_echo`, `duplicate`, `stale_after`.

## Verdict

**Verified by Monica on 2026-11-27** by reading the code and running all verification commands herself.
Result: **done**. Nothing sent back.

Commands run: `manage.py check` (0 issues), `makemigrations --check --dry-run` (No changes detected), `pytest -q` (**171 passed**), `pytest -m brief` (**9 passed**), `bash scripts/build-wiki.sh` (7 pages).

| AC | How verified | |
|---|---|---|
| AC-4.1.1 | FakeSalesforce in-memory store: idempotent upsert, update with KeyError on missing, changed-since paging | ✅ |
| AC-4.1.2 | MockTransport dispatches upsert, update, and query endpoints without network sockets | ✅ |
| AC-4.1.3 | Live ThreadingHTTPServer responds identically to real HTTP requests on localhost | ✅ |
| AC-4.2.1 | All 6 failure modes (down, slow, throttle 429, error 500, reject 400, auth 401) verified | ✅ |
| AC-4.2.2 | Call log accurately records method, URL, body, and timestamp | ✅ |
| AC-4.3.1 | Scenario builders produce valid payloads (changed fields only and whole record fallback) | ✅ |
| AC-4.3.2 | Duplicate and stale_after helpers function as specified | ✅ |
| AC-4.3.3 | Brief test marker passes (9 total brief tests); scenario post to webhook updates portal DB | ✅ |
| AC-X | 171 tests pass, check clean, no real network dependencies | ✅ |

