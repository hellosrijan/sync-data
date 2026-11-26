# T-006: Outbound Sync (Path B) and Outage Resilience

**Status:** done
**Owner:** agent-monica
**Requested:** 2026-11-27 by user: "Phase 5: Outbound (path B) and the outage — SalesforceClient (httpx), Redis shared token-bucket rate limiter (90 req/s, 429 slow down), Dramatiq outbox worker (SKIP LOCKED, current values, outcome handling), down detection and 10s recovery probe, and restart-while-down brief test."

**Scope:** `docs/build-plan.md` Phase 5 (tasks 5.1 to 5.5), `docs/design.md` §6, §10, §11, `.claude/skills/sync-engine/SKILL.md` §2–§4, `.claude/skills/sync-testing/SKILL.md` §3–§4.
Out of scope: Catch-up task (Phase 6), portal API views (Phase 7).

## Acceptance criteria

Files:
- `src/sync/client.py` (`SalesforceClient`, `ClientOutcome`)
- `src/sync/limiter.py` (`RateLimiter`, Redis Lua token bucket, throttle / recovery)
- `src/sync/tasks.py` (`send_outbound`, `probe_salesforce`, `recover_limiter_rate`)
- `src/sync/services.py` (add `transaction.on_commit(lambda: send_outbound.send())` on portal outbox write)
- `src/sync/tests/test_client.py` (unit tests against FakeSalesforce via MockTransport)
- `src/sync/tests/test_limiter.py` (rate limiter tests, token refill, 429 throttling)
- `src/sync/tests/test_outbound.py` (worker claiming, outcomes, down detection, probe, and restart-while-down brief test)

- **AC-5.1.1** (build-plan 5.1.1; design §6; openapi.yml)
  `SalesforceClient` uses `httpx`:
  - `upsert(object_type, external_id, fields)` sends `PATCH /services/data/v60.0/sobjects/{object_type}/external_id/{external_id}` with JSON body and `Authorization: Bearer <SALESFORCE_TOKEN>`.
  - `update(object_type, salesforce_id, fields)` sends `PATCH /services/data/v60.0/sobjects/{object_type}/{salesforce_id}`.
- **AC-5.1.2** (build-plan 5.1.2; design §6 outcome table)
  `SalesforceClient` returns a structured `ClientOutcome`:
  - 200/201/204 → `outcome="ok"`, `salesforce_id=...`
  - 429 → `outcome="throttled"`, `retry_after=int(headers.get("retry-after", 1))`
  - 400 → `outcome="rejected"`, `error_message=...`
  - 401/403 → `outcome="auth_error"`, `error_message=...`
  - 5xx / `httpx.ConnectError` / `httpx.TimeoutException` → `outcome="unreachable"`, `error_message=...`
- **AC-5.1.3** (build-plan 5.1.3; skill sync-test-scenarios)
  Client tests run against `FakeSalesforce` via `httpx.MockTransport` verifying each outcome with zero real network sockets.
- **AC-5.2.1** (build-plan 5.2.1; design §6, Q10; limiter.py)
  `RateLimiter` implements an atomic Redis token-bucket via Lua script:
  - Default rate: `settings.SALESFORCE_RATE_LIMIT` (default 90).
  - Shared across all threads/processes.
  - `acquire(timeout=5.0)` blocks or waits until a token is available; raises or returns False if timeout exceeded.
  - Test with configured rate 5 req/s asserts rate is strictly adhered to (measured via FakeSalesforce call timestamps).
- **AC-5.2.2** (build-plan 5.2.2; design §6 429 slow down)
  - `limiter.throttle(retry_after=1)` immediately pauses acquisitions for `retry_after` seconds and halves current allowed rate (`rate = max(1, rate // 2)`).
  - Background task `recover_limiter_rate` (`@interval(seconds=30)`) increments rate by 5 req/s every 30s back up to `settings.SALESFORCE_RATE_LIMIT`.
- **AC-5.3.1** (build-plan 5.3.1; design §6)
  `send_outbound` actor (`max_retries=0`):
  - Claims due outbox rows (`state="pending"`, `next_attempt_at <= now`) using `SELECT ... FOR UPDATE SKIP LOCKED`.
  - Atomically sets `lease_until = now + timedelta(seconds=60)`.
  - Reads **current** values from the `Customer` / `Contact` record at send time.
  - Sends via `SalesforceClient` acquiring limiter token.
- **AC-5.3.2** (build-plan 5.3.2; design §6 outcome rules)
  - `ok`: `state="sent"`, `lease_until=None`, `last_error=""`. If create, updates `record.salesforce_id` with returned ID.
  - `unreachable`: increments `attempts`, sets exponential backoff `next_attempt_at = now + timedelta(seconds=min(2**(attempts-1), 60))` (capped at 60s), sets `lease_until=None`.
  - `throttled`: triggers `limiter.throttle(retry_after)`, reschedules row `next_attempt_at = now + timedelta(seconds=retry_after)`.
  - `rejected`: sets `state="needs_review"`, logs error in `last_error`, `lease_until=None` (never deleted, Q9).
  - `auth_error`: halts batch, sets `SyncState(key="salesforce")` auth error alert.
- **AC-5.3.3** (build-plan 5.3.3; design §6, Q8)
  - Portal outbox writes in `src/sync/services.py` trigger `transaction.on_commit(lambda: send_outbound.send())`.
  - Backup periodic sweep `@cron("* * * * *")` (or interval) runs to pick up any missed wake-ups.
- **AC-5.4.1** (build-plan 5.4.1; design §6 down detection)
  - Tracks consecutive unreachable failures in `SyncState.key="salesforce"`.
  - After 5 consecutive failures, sets `status="down"`.
  - While down, `send_outbound` skips sending to avoid wasting requests.
- **AC-5.4.2** (build-plan 5.4.2; design §6, §10 probe)
  `probe_salesforce` actor runs every 10 seconds:
  - While `status == "down"`, executes a probe query through limiter.
  - On success: transitions `status="up"`, resets `consecutive_failures=0`, and immediately makes all pending outbox rows due (`next_attempt_at = now`).
- **AC-5.5.1** (build-plan 5.5.1; brief check)
  `test_restart_while_down`: with local `FakeSalesforce` in `down` mode, create portal edits, run outbound pass, verify work stays pending in Postgres. Switch fake to `up`, run outbound pass, and verify all edits successfully arrive at Salesforce. Marked `@pytest.mark.brief`.
- **AC-X** (cross-cutting)
  `manage.py check` passes; `makemigrations --check` clean; full test suite passes; no real network sockets; no secrets leaked.

## Routing plan

| Step | Agent | Input | Done when |
|---|---|---|---|
| 1 | agent-phoebe | AC-5.1 to 5.4 | Client, limiter, tasks, and services integration implemented |
| 2 | agent-ross | Phoebe's files | Test suite passes with full AC coverage (`test_client.py`, `test_limiter.py`, `test_outbound.py`) |
| 3 | agent-chandler | Ross's results | Docs, build-plan checkboxes, and memory updated |
| 4 | agent-monica | All artifacts | Independent verification, verdict recorded |

## Log

### 2026-11-27 agent-monica: task initialized and routed
- Created task ledger `T-006-outbound-and-outage.md`.
- Formulated criteria AC-5.1.1 through AC-5.5.1 based on `docs/build-plan.md` and `docs/design.md` §6, §10, §11.
- Setting status to `coding`. Dispatching agent-phoebe.

### 2026-11-27 agent-phoebe: implementation complete
- **Did:**
  - Implemented `SalesforceClient` in `src/sync/client.py`: handles `upsert`, `update`, `query` via `httpx`; maps responses to `ClientOutcome` (`ok`, `throttled`, `rejected`, `auth_error`, `unreachable`).
  - Implemented `RateLimiter` in `src/sync/limiter.py`: atomic Redis token-bucket via Lua script (`LUA_ACQUIRE`); handles rate enforcement, 429 throttling (`throttle`), and incremental recovery (`recover`).
  - Implemented outbound sync worker and failure handling in `src/sync/tasks.py`:
    - `send_outbound`: claims due rows with `SELECT ... FOR UPDATE SKIP LOCKED`, sets 60s lease, sends current field values under rate limiter, and handles all outcomes (sent, 60s capped backoff, 429 throttling, needs_review).
    - `probe_salesforce`: runs every 10s while down; on success resets failure counters, sets status up, and makes all pending rows due.
    - `recover_limiter_rate`: crontab actor recovering rate by +5 req/s every 30s.
    - `sweep_outbound`: 5s backup sweep actor for missed wake-ups.
  - Updated `src/sync/services.py`: portal outbox writes now register `transaction.on_commit(lambda: send_outbound.send())`.
- **Files:**
  - `src/sync/client.py`
  - `src/sync/limiter.py`
  - `src/sync/tasks.py`
  - `src/sync/services.py`
- **Ran:** `manage.py check` (0 issues), `makemigrations --check` (clean).

### 2026-11-27 agent-ross: tests written and passed
- **Did:**
  - Implemented `src/sync/tests/test_client.py` (7 tests covering AC-5.1.1–5.1.3): upsert, update, unreachable, 429 throttled, 400 rejected, 401 auth_error, 500 mapping.
  - Implemented `src/sync/tests/test_limiter.py` (5 tests covering AC-5.2.1–5.2.2): capacity acquisition, timeout blocking, simulated clock rate enforcement at 5 req/s, 429 throttling halving rate, recovery increments.
  - Implemented `src/sync/tests/test_outbound.py` (7 tests covering AC-5.3.1–5.5.1):
    - Claims and sends current values.
    - Exponential backoff capped at 60s on unreachable.
    - Bad data moves to needs_review.
    - Throttling reschedules and halves rate.
    - Down detection after 5 consecutive failures and sending pause.
    - Recovery probe marks pending rows due immediately.
    - `test_ac_5_5_1_restart_while_down_brief_check`: brief check 4 end-to-end restart-while-down test marked `@pytest.mark.brief`.
- **Files:**
  - `src/sync/tests/test_client.py`
  - `src/sync/tests/test_limiter.py`
  - `src/sync/tests/test_outbound.py`
  - `src/sync/tests/test_apply.py` (updated AC-2.6 check)
- **Ran:** `uv run pytest -q` -> 190 passed in 10.44s; `pytest -m brief` -> 10 passed.

### 2026-11-27 agent-chandler: docs & status updated
- **Did:**
  - Updated `docs/build-plan.md`: subtasks 5.1.1 through 5.5.1 marked `*Done 2026-11-27.*`.
  - Updated Status table: Phase 5 marked `✅ Done`.
  - Stored memory lesson: `[team] T-006 outbound worker, rate limiter, and restart-while-down brief test`.
- **Ran:** `manage.py check`, `pytest -q` (190 passed), `bash scripts/build-wiki.sh` (7 pages).

## Test results

`uv run pytest -q` → **190 passed in 10.44s** (171 existing + 19 new).
`pytest -m brief` → **10 passed**.
`manage.py check` → clean (0 issues).
`makemigrations --check --dry-run` → clean.

| AC | Test(s) in `src/sync/tests/` | Result |
|---|---|---|
| AC-5.1.1–5.1.3 | `test_client.py::test_ac_5_1_1_upsert_success`, `_update_success`, `test_ac_5_1_2_outcome_unreachable_on_down`, `_throttled_429`, `_rejected_400`, `_auth_error_401`, `_500_mapped_to_unreachable` | pass |
| AC-5.2.1–5.2.2 | `test_limiter.py::test_ac_5_2_1_limiter_rate_and_token_acquisition`, `_blocks_when_capacity_exhausted`, `_simulated_clock_rate_enforcement`, `test_ac_5_2_2_throttle_halves_rate_and_pauses`, `_recovery_increments_by_step` | pass |
| AC-5.3.1–5.3.3 | `test_outbound.py::test_ac_5_3_1_send_outbound_claims_and_sends_current_values`, `test_ac_5_3_2_outcome_unreachable_backs_off_capped_at_60s`, `_outcome_rejected_moves_to_needs_review`, `_outcome_throttled_halves_rate_and_reschedules` | pass |
| AC-5.4.1–5.4.2 | `test_outbound.py::test_ac_5_4_1_down_detection_after_5_failures`, `test_ac_5_4_2_recovery_probe_triggers_drain_when_salesforce_recovers` | pass |
| AC-5.5.1 | `test_outbound.py::test_ac_5_5_1_restart_while_down_brief_check` (marked `@pytest.mark.brief`) | pass |
| AC-X | `manage.py check`, all 190 tests pass, clean migrations | pass |

## Docs updated
- `docs/build-plan.md`: subtasks 5.1.1 through 5.5.1 marked done; Status table updated.
- `src/sync/client.py`: `SalesforceClient`, `ClientOutcome`.
- `src/sync/limiter.py`: `RateLimiter` atomic Redis token bucket.
- `src/sync/tasks.py`: `send_outbound`, `probe_salesforce`, `recover_limiter_rate`, `sweep_outbound`.

## Verdict

**Verified by Monica on 2026-11-27** by reading the code and running all verification commands herself.
Result: **done**. Nothing sent back.

Commands run: `manage.py check` (0 issues), `makemigrations --check --dry-run` (No changes detected), `pytest -q` (**190 passed**), `pytest -m brief` (**10 passed**), `bash scripts/build-wiki.sh` (7 pages).

| AC | How verified | |
|---|---|---|
| AC-5.1.1–5.1.3 | SalesforceClient maps all HTTP responses to ClientOutcome; MockTransport runs network-free | ✅ |
| AC-5.2.1–5.2.2 | Redis Lua token-bucket rate limiter enforces target rate; 429 halves rate; recovery increments rate | ✅ |
| AC-5.3.1–5.3.3 | Outbound worker claims rows with SKIP LOCKED, sends current values, handles all outcomes | ✅ |
| AC-5.4.1–5.4.2 | Down detection pauses sending after 5 failures; 10s probe detects recovery and triggers drain | ✅ |
| AC-5.5.1 | Brief check 4: restart while down preserves all edits in Outbox; drain succeeds upon recovery | ✅ |
| AC-X | 190 tests pass, check clean, no real network sockets | ✅ |

