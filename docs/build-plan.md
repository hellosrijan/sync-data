# Build plan

How the design in [`design.md`](design.md) gets built, in phases, by the agent team
(`/monica <task>`, see `.claude/rules/agent-team.md`). Brief: [`Round1.html`](Round1.html).
The API contract is [`openapi.yml`](openapi.yml).

Each phase has tasks, and each task has subtasks. Every task names its **owner**, what it
**depends on**, and when it's **done**. Monica runs one task at a time as a `/monica` task:
Joey turns the task's subtasks into acceptance criteria, Phoebe builds, Ross tests, Chandler
documents, Monica verifies.

**This file is also the checklist.** Every subtask is a checkbox. The agents keep it up to date
(rules in `.claude/rules/agent-team.md`, "The build checklist"):

| Marker | Meaning |
|---|---|
| `- [ ] 1.2.3 …` | Open |
| `- [ ] 1.2.3 … **In progress.**` | Monica has started it |
| `- [x] 1.2.3 … *Done YYYY-MM-DD.*` | Verified by Monica, with evidence in the task ledger |
| `- [x] ~~1.2.3 …~~ *Cancelled: reason.*` | Dropped |
| `` `defaulted` `` after an item | Built on a default from the table below; you review it in "Defaulted decisions to review" |

Overall status per phase is in [Status](#status) at the end.

## How work keeps moving

The user asked that work never stops for a question unless it's critical.

- **Not critical: decide and continue.** If an agent meets an open question, it takes the
  default in the table below (or the most conservative option consistent with the brief). It
  records the choice as an assumption in `design.md` §1 and as `DEFAULTED: …` in the task
  ledger, then carries on. The user reviews defaulted decisions at the end of the phase.
- **Critical: stop and ask.** Only when the next step would:
  1. contradict the brief or its numbers (5M records, 30 s, ≤ 100 req/s, 6-hour outage);
  2. reverse a decision the user made (anything marked "Decided (user)" in `design.md`);
  3. be irreversible or outward-facing (deleting data, rewriting git history, pushing, calling
     anything outside this machine);
  4. weaken security (secrets, authentication, permissions).

### Defaults for questions still open

| # | Question | Default (recorded as an assumption) |
|---|---|---|
| Q14 | How do portal users authenticate? | DRF `TokenAuthentication`, one token per portal user, same mechanism as the webhook (Q11). No UI this weekend, so no sessions or SSO. |
| Q15 | Setting for integration user (echo detection) | `SALESFORCE_INTEGRATION_USER` in `settings.py` (env-backed, placeholder default; design §1 Q15). |
| Q16 | Unknown record on portal save or Salesforce update | Raise `UnknownRecord` and roll back transaction; nothing created, `ChangeEvent` not kept (design §1 Q16). |
| Q17 | Echo on create for unknown record | Never create a record; raise `UnknownRecord` and roll back (design §1 Q17). |
| Q18 | Merging portal update into pending create outbox row | Keeps `operation="create"`, merging fields as a sorted union (design §1 Q18). |
| Q19 | A contact whose customer isn't in the portal yet | Store the customer's Salesforce ID in `customer_salesforce_id`. Link the FK when that customer arrives (design §1 Q19, T-004). |
| Q20 | Inbound `null`/`None` for optional string fields | Sanitize `None` to empty string `""` on non-nullable CharFields, TextFields, EmailFields (design §1 Q20, T-004). |
| — | Build order (design §3) | This plan's phase order, riskiest first (see below). |
| — | Where is "what changed" worked out for an inbound webhook? | **Inside** `apply()`, after `SELECT … FOR UPDATE`, so two webhooks for one record can't both compare against the same old value. (A correction found in review.) |
| — | Echo detection on catch-up | Field by field: a field whose Salesforce value equals the value we last sent is our echo. Not record-level. (A correction found in review.) |
| — | Catch-up gives only a record-level modified time | Accept as a known limit (design "Known limits"). Catch-up only runs after an outage, or as the 5-minute safety net. |
| — | Payload key for an inbound webhook | Keep the brief's `changedFields` (`openapi.yml`), carrying only the changed fields (Q2, revised 2026-11-27 to match the brief's sample). |
| — | `owner` and `status` values | Free text, as Salesforce sends them (owner = Salesforce user ID or name). |
| — | A contact whose customer isn't in the portal yet | Store the customer's Salesforce ID in `customer_salesforce_id`. Link the FK when that customer arrives. |
| — | A portal contact created before its customer has a Salesforce ID | The upsert refers to the customer by external ID (part of Q13). No ordering wait. |
| — | HTTP client for Salesforce | `httpx`, wrapped in one `SalesforceClient` class so tests swap in a fake. |
| — | Time in tests | Inject a clock (`now()` parameter or setting). No freezegun. |

### Defaulted decisions to review

Tick one when you accept it. To change one, tell Monica; she updates the plan and `design.md`.

- [ ] Q14 portal login: a DRF token per portal user
- [ ] "What changed" is worked out inside the record lock
- [ ] Catch-up echo check is field by field
- [ ] Echo setting: `SALESFORCE_INTEGRATION_USER` (name and placeholder value, not in the brief) (T-003, Q15)
- [ ] Unknown record: a portal change for a missing pk, or a Salesforce update for an unknown id, raises `UnknownRecord` and rolls back, instead of creating a row (T-003, Q16)
- [ ] An echo never creates a record: a Salesforce `create` echo for an unknown id raises instead of creating (T-003, Q17)
- [ ] A portal update merged into a pending `create` outbox row keeps `create`, not `update` (T-003, Q18)
- [ ] Catch-up's record-level modified time is accepted as a known limit
- [ ] Requeue (admin action) skips a `needs_review` row whose record already has a pending row, with a warning message, rather than failing on the partial unique constraint (T-001)
- [ ] Serializers render camelCase names now (explicit `source=`, per `openapi.yml`) instead of in phase 7 (T-001)
- [ ] `Outbox.record_id` and `FieldChange.record_id` are `BigIntegerField`, not foreign keys, because they point at either table (T-001)
- [ ] `created_by` / `updated_by` default to an empty string, not NULL (T-002)
- [x] The contract (`openapi.yml`) lists only fields the API returns today, so `syncState` is out of the schema until phase 7 (7.1.1). Decided by the user, 2026-11-27 (option A).
- [ ] `create_integration_user` prints the Salesforce token on **every** run (build-plan 1.2.3 said "once"). Recommended: print it only when it creates the token. Not changed yet; your call.
- [ ] Contact parent customer resolution uses `customer_salesforce_id` to link asynchronously without requiring Customer to precede Contact (T-004, Q19)
- [ ] Missing/cleared optional string fields in `apply()` sanitize `None` to `""` on non-nullable CharFields (T-004, Q20)

## Why this order (design §3)

The brief grades the order: *"which part is risky, and why you take that part before the
rest."*

1. **`apply()` comes first (phase 2).** Every rule the brief checks runs inside it: duplicate,
   out-of-order, sync loop, history. If it's wrong, nothing built on top can be right, and it
   needs no network, so it's the cheapest place to prove the rules.
2. **Outbound and outage come next (phases 4–5).** This is where data gets lost if we're wrong:
   the 6-hour outage, retries counted in the cap, surviving restarts. Building the test
   scenarios first (a fake Salesforce inside the test suite) makes outages, 429s and
   rejections reproducible on demand.
3. **Catch-up (phase 6)** depends on both of the above. **The portal API (phase 7)** is a thin
   layer of generic views over `apply()`, so it's low risk and goes late.
4. **The demo (phase 8)** proves the four brief checks end to end. **Submission (phase 9)** is
   last.

Phase 1 (foundations) comes before all of these, because every later phase needs the apps,
models and contract.

## Apps

| App | Holds | Why separate |
|---|---|---|
| `sync` (exists) | Customer, Contact, ChangeEvent, FieldChange, Outbox, SyncState; `services.py` (`apply()`, `Change`, `Result`, rules core); the webhook view; the portal API views; Dramatiq tasks (outbound, probe, catch-up); the limiter; `SalesforceClient` | The sync rules and the records they protect change together. One migration chain. |
| Test code, **not an app**: `src/sync/tests/` | `fakes.py` (`FakeSalesforce`, a test double for Salesforce) and `scenarios.py` (webhook payload builders). Imported only by tests and `scripts/demo.sh`. | User decision: no fake-Salesforce app or container, because it would be confusing. The fake lives with the tests that use it. |
| `utils` (exists) | `/api/v1/health/` (moved under the versioned prefix), the `rundramatiq` override | `urls.py` and `serializers.py` added |

## Models (portal database)

Full column layout: `design.md` §4. In short:

| Model | Key columns | Constraints and indexes |
|---|---|---|
| `Customer` | `external_id` (UUID), `salesforce_id`, `name`, `status`, `owner`, `field_meta` (JSONB: per field `at`, `source`, `by`, `sf_value`) | unique `external_id`; unique `salesforce_id` (nullable) |
| `Contact` | same sync columns + `full_name`, `phone`, `email`, `city`, `owner`, `customer` (FK, nullable), `customer_salesforce_id` | unique `external_id`, `salesforce_id`; index `customer`, `customer_salesforce_id`, `email` |
| `ChangeEvent` | `event_id`, `source`, `received_at` | **unique `event_id`** (dedupe, §8); index `received_at` (7-day purge) |
| `FieldChange` | `object_type`, `record_id`, `field`, `value`, `actor`, `source`, `occurred_at`, `status` (applied/superseded/echo), `event_id` | index `(object_type, record_id, occurred_at)`; append-only |
| `Outbox` | `object_type`, `record_id`, `operation` (create/update), `fields` (array), `state`, `attempts`, `next_attempt_at`, `lease_until`, `last_error` | **one pending row per record** (partial unique); index `(state, next_attempt_at)` |
| `SyncState` | `key`, `value` (JSONB) | Watermark, Salesforce up/down, consecutive failures |

## URL conventions (user decision)

- `config/urls.py` is the root URLconf. It mounts everything under **`api/v1/`**, defined once
  there; no app repeats the prefix.
- Resource names are **singular**: `api/v1/customer/`, `api/v1/contact/`.
- **Every app has its own `urls.py`** (with `app_name`) **and `serializers.py`**, and
  `config/urls.py` includes each app's `urls.py`. Views are added to an app's `urls.py` as
  later phases build them.
- `/health/` moved to `api/v1/health/`: all API paths are versioned.

## Endpoints

`openapi.yml` is the contract. All are DRF generic views:

| Endpoint | View | Auth |
|---|---|---|
| `POST /api/v1/webhooks/salesforce/` | `CreateAPIView` (answers 200) | Integration-user token (Q11) |
| `GET, POST /api/v1/customer/` | `ListCreateAPIView` (cursor) | Portal-user token (Q14) |
| `GET, PATCH /api/v1/customer/{id}/` | `RetrieveUpdateAPIView` | Portal-user token |
| `GET /api/v1/customer/{id}/history/` | `ListAPIView` | Portal-user token |
| `GET, POST /api/v1/contact/`, `GET, PATCH /api/v1/contact/{id}/`, `GET /api/v1/contact/{id}/history/` | Same as customers | Portal-user token |
| `GET /api/v1/sync/status/` | `RetrieveAPIView` | Staff only |
| `GET /api/v1/health/` | `RetrieveAPIView` (exists) | Public |

---

## Phase 1: Foundations

**Goal:** the team can build safely, and the apps, models, admin and contract exist.
**Exit:** `manage.py check`, `makemigrations --check` and `pytest` pass. The admin lists every
model. `openapi.yml` validates.

### 1.1 Close the team's skill gaps
Owner: main session (no `/monica`, because it changes the team itself). Depends on: nothing.
- [x] 1.1.1 Write `.claude/skills/sync-engine/SKILL.md`: `apply()` structure, row locking,
  per-field meta, the outbox worker (`SKIP LOCKED`, leases, Dramatiq retries off), the Redis
  token-bucket limiter, the probe and down state, and catch-up. *Done 2026-11-27.*
- [x] 1.1.2 Write `.claude/skills/sync-test-scenarios/SKILL.md`: the `FakeSalesforce` test double
  (state, failure modes, `httpx.MockTransport`, local-server fixture), the scenario builders,
  and the `brief` pytest marker. *Done 2026-11-27.*
- [x] 1.1.3 Write `.claude/skills/sync-testing/SKILL.md`: injected clock, transactional
  concurrency tests, restart-while-down, measuring the request rate, the brief's four checks. *Done 2026-11-27.*
- [x] 1.1.4 Update `.claude/rules/agent-team.md` with the "keep moving" policy above, and replace
  the "portal auth isn't decided" line in `drf.md` with Q14. *Done 2026-11-26.*
- [x] 1.1.5 Extend the export block in `.claude/agents/chat-export.md` to include subagent chats
  (`<session>/subagents/*.jsonl`), so every prompt the team sends is in the submission. The
  same redaction applies. *Done 2026-11-27.*

**Done when:** the three skills exist; the rules say decide-and-continue; a test export
includes subagent chats. **Done 2026-11-27**: verified a test export of 31 real subagent
transcripts from this session's own history, confirmed no real secrets leaked (including a
targeted redaction added for the one command — `create_integration_user` — that prints a bare
secret with no label), and confirmed the fix for subagents' own `isSidechain: true` tagging
(the main-session filter would otherwise have dropped every subagent message).

### 1.2 Settings and authentication
Owner: Phoebe → Ross. Depends on: 1.1.
- [x] 1.2.1 Add `rest_framework.authtoken` to `INSTALLED_APPS` and migrate. *Done 2026-11-27.*
- [x] 1.2.2 DRF defaults: `TokenAuthentication`, `IsAuthenticated`. `/api/v1/health/` stays `AllowAny`. `defaulted` (Q14) *Done 2026-11-27.*
- [x] 1.2.3 Integration user: a group `salesforce-integration`, a permission class
  `IsSalesforceIntegration`, and a `create_integration_user` command that prints the token once. *Done 2026-11-27.*
- [x] 1.2.4 `uv add httpx`. Settings from env: `SALESFORCE_BASE_URL`, `SALESFORCE_TOKEN`,
  `SALESFORCE_RATE_LIMIT` (default 90), with the same keys in `.env` and `.env.example`. *Done 2026-11-27.*

**Done when:** a request without a token gets 401 and `/api/v1/health/` gets 200 (tests); `.env` and
`.env.example` have the same keys.

### 1.3 Models and migrations
Owner: Phoebe → Ross. Depends on: 1.2.
- [x] 1.3.1 `Customer` and `Contact` with the sync columns and the `field_meta` JSONB; a contact's customer link is `customer_salesforce_id` plus a nullable FK. `defaulted` *Done 2026-11-27.*
- [x] 1.3.2 `ChangeEvent` (unique `event_id`), `FieldChange` (append-only), `Outbox` (partial
  unique on one pending row per record), `SyncState`. *Done 2026-11-27.*
- [x] 1.3.3 Indexes from the table above, in the same migrations. *Done 2026-11-27.*
- [x] 1.3.4 Model tests: the unique constraints hold at database level (a duplicate `event_id`
  raises, and so does a second pending outbox row for one record). *Done 2026-11-27.*

**Done when:** migrations apply on an empty database, `makemigrations --check` is clean, and
the constraint tests pass.

### 1.4 Django admin
Owner: Phoebe → Ross. Depends on: 1.3.
- [x] 1.4.1 Customer/Contact: `list_display`, `search_fields` on indexed columns only,
  `list_select_related`, `raw_id_fields` for `customer`, `show_full_result_count = False`. *Done 2026-11-27.*
- [x] 1.4.2 `FieldChange` and `ChangeEvent`: read-only (no add, change or delete). *Done 2026-11-27.*
- [x] 1.4.3 `Outbox`: read-only, plus a "requeue" action for `needs_review` rows (Q9); it skips a record that already has a pending row. `defaulted` *Done 2026-11-27.*

**Done when:** Ross's admin tests pass: every changelist, change page and search returns 200,
and append-only models refuse writes.

### 1.5 API contract in the docs
Owner: Chandler. Depends on: 1.3.
- [x] 1.5.0 Write `docs/openapi.yml`: valid OpenAPI 3.1, 9 paths, 13 operations. *Done 2026-11-26.*
- [x] 1.5.1 Keep `docs/openapi.yml` in line with the models (field names, lengths). *Done 2026-11-27.*
- [x] 1.5.2 Fill `design.md` §4 (column layout) and add Q14 to §1. *Done 2026-11-26.*

**Done when:** `openapi.yml` validates and `design.md` §4 has no TODO.

### 1.6 URL layout and serializers
Owner: Phoebe → Ross. Depends on: 1.2, 1.3.
- [x] 1.6.1 `config/urls.py` mounts `api/v1/` once and includes each app's `urls.py`
  (`utils` first, then `sync`). `/health/` moves to `api/v1/health/`. *Done 2026-11-27.*
- [x] 1.6.2 `urls.py` with `app_name` in every app: `utils` (health) and `sync` (empty
  `urlpatterns` until phases 3 and 7 add views, with a comment listing the planned routes). *Done 2026-11-27.*
- [x] 1.6.3 `serializers.py` in every app: `sync` gets `CustomerSerializer`,
  `ContactSerializer` and `FieldChangeSerializer` (explicit `fields`, server-owned fields
  read-only, field names as in `openapi.yml`); `utils` gets `HealthSerializer` (moved out of
  `views.py`); camelCase names now. `defaulted` *Done 2026-11-27.*
- [x] 1.6.4 Tests: `/api/v1/health/` returns 200, the old `/health/` returns 404, and
  `reverse()` works for each named route; serializer tests for the field lists and read-only
  fields. *Done 2026-11-27.*

**Done when:** `manage.py check` is clean, the tests pass, and `config/urls.py` is the only
place that mentions `api/v1/`.

### 1.7 Audit columns and model docstrings (T-002)
Owner: Phoebe → Ross. Depends on: 1.3, 1.6. Added at the user's request ("are we saving created_by?").
- [x] 1.7.1 `created_by` and `updated_by` on `Customer` and `Contact` (in the shared base):
  `CharField(255, blank=True, default="")`, one migration (`0002_audit_columns`). Nothing sets
  them yet; `apply()` does in phase 2 (see 2.2.4). `defaulted` *Done 2026-11-27.*
- [x] 1.7.2 Expose them read-only in the API: `createdAt`, `createdBy`, `updatedBy` (and the
  existing `updatedAt`) in `CustomerSerializer` and `ContactSerializer`, matching `openapi.yml`.
  Show them in the admin (read-only). *Done 2026-11-27.*
- [x] 1.7.3 Docstrings: every model class and every non-obvious field in `src/sync/models.py`
  says what it is for, its constraints, and where the design describes it. *Done 2026-11-27.*
- [x] 1.7.4 Tests: the columns exist with the right type, length and default; the serializers
  return the three new fields read-only; the admin shows them. *Done 2026-11-27.*

**Done when:** `check`, `makemigrations --check` and `pytest` pass, and the serializer field
sets still equal the `openapi.yml` schemas.

---

## Phase 2: `apply()`, the rules core (riskiest)

**Goal:** every rule the brief checks works at database level, with no network.
**Exit:** tests for the duplicate, out-of-order, sync-loop, history and concurrency cases pass.

### 2.1 The `Change` input and `apply()` skeleton
Owner: Phoebe → Ross. Depends on: 1.3.
- [x] 2.1.1 A `Change` dataclass: `source`, `event_id`, `object_type`, `record_ref` (Salesforce ID
  or portal ID), `occurred_at`, `actor`, `fields`, `operation`. *Done 2026-11-27.*
- [x] 2.1.2 `apply()` in one transaction: insert into `ChangeEvent` (a duplicate stops here),
  then `SELECT … FOR UPDATE` the record (creating it if new). An unknown portal pk or an unknown
  Salesforce id on an update raises and rolls back (`defaulted`, Q16); an echo never creates
  (`defaulted`, Q17). *Done 2026-11-27.*

**Done when:** sending the same `event_id` twice gives one `ChangeEvent`, and the second call
reports `duplicate`.

### 2.2 Per-field rules
Owner: Phoebe → Ross. Depends on: 2.1.
- [x] 2.2.1 Salesforce source: work out the changed fields **inside the lock**, comparing each
  value with `field_meta[f].sf_value` (Q2). *Done 2026-11-27.*
- [x] 2.2.2 Echo: if `actor` is the integration user, record the change as `echo` and apply nothing
  to the record column, history status or outbox — `field_meta[f].sf_value` is still kept in sync
  with the echoed value (gated by `_is_newer`, T-007). The setting is `SALESFORCE_INTEGRATION_USER`
  with a placeholder value (`defaulted`, Q15). *Done 2026-11-27.*
- [x] 2.2.3 For each changed field: always write a `FieldChange`. Apply it if `occurred_at` is newer
  than the field's stored time, otherwise mark it `superseded`. An exact tie goes to Salesforce. *Done 2026-11-27.*
- [x] 2.2.4 Update `field_meta`: `at`, `source`, `by`, and `sf_value` for Salesforce changes. Also
  set `created_by` (on create) and `updated_by` (on every applied change) on the record. *Done 2026-11-27.*

**Done when:** these tests pass:
- the brief's scenario: the city set in the portal survives a later phone webhook;
- an older event after a newer one leaves the current value but adds a history row;
- two fields edited at once both survive;
- a tie goes to Salesforce.

### 2.3 Outbox write and the loop rule
Owner: Phoebe → Ross. Depends on: 2.2.
- [x] 2.3.1 A portal change with at least one applied field → upsert the record's single `pending`
  outbox row, merging the field list. *Done 2026-11-27.*
- [x] 2.3.2 A Salesforce change → never an outbox row. *Done 2026-11-27.*
- [x] 2.3.3 A portal create → an outbox row with `operation=create`. A later portal update merged into that pending row keeps `create` (`defaulted`, Q18). *Done 2026-11-27.*

**Done when:**
- a webhook write creates 0 outbox rows;
- 3 portal edits to one record leave 1 pending row with the union of fields;
- the edit and its outbox row commit together, or neither does.

### 2.4 Concurrency
Owner: Ross. Depends on: 2.3.
- [x] 2.4.1 Transactional test: two threads apply changes to the same record, and the final state
  and history are correct. *Done 2026-11-27.*
- [x] 2.4.2 Two webhooks for one record at the same time (each carrying one or several changed
  fields): no lost field. *Done 2026-11-27.*

**Done when:** both tests pass reliably (10 runs in a row).

---

## Phase 3: Inbound webhook (path A)

**Goal:** Salesforce changes land in Postgres through the endpoint in `openapi.yml`.
**Exit:** contract tests for the webhook pass.

### 3.1 Webhook serializer and view
Owner: Phoebe → Ross. Depends on: 2.3, 1.2.
- [x] 3.1.1 `WebhookEventSerializer`: required fields, `objectType` enum, ISO `occurredAt`; fields
  outside the brief's scope are ignored. *Done 2026-11-27.*
- [x] 3.1.2 A `CreateAPIView` that overrides `create` to call `apply()` and answer **200** with
  `WebhookResult`. *Done 2026-11-27.*
- [x] 3.1.3 Permission: `IsSalesforceIntegration` with `TokenAuthentication` first, so a bad token
  gets 401. *Done 2026-11-27.*
- [x] 3.1.4 A contact webhook whose `customer` isn't known yet: store `customer_salesforce_id`.
  When the customer arrives, link waiting contacts. *Done 2026-11-27.*

**Done when:**
- 401 without a token, 403 with a portal user's token, 400 on a malformed payload, and
  nothing stored in any of those cases;
- 200 with `applied`, `duplicate`, `echo`, `superseded` or `no_change` in the right cases;
- the brief's sample payload works.

### 3.2 Dedupe retention
Owner: Phoebe → Ross. Depends on: 3.1.
- [x] 3.2.1 A daily dramatiq-crontab job deletes `ChangeEvent` rows older than 7 days. *Done 2026-11-27.*

**Done when:** a test with an injected clock shows an 8-day-old row removed and a 6-day-old row
kept.

---

## Phase 4: Test scenarios (a fake Salesforce inside the tests)

**Goal:** every Salesforce behaviour the design depends on, including breaking it on demand,
is available to tests, with **no extra app or container** (user decision). The brief asks us to
*"call a fake CRM you write yourself"*; it lives in the test suite.
**Exit:** `FakeSalesforce` and the scenario builders exist, and each failure mode has a test.

### 4.1 `FakeSalesforce` test double
Owner: Phoebe → Ross. Depends on: 1.1.2.
- [x] 4.1.1 `src/sync/tests/fakes.py`: in-memory records keyed by Salesforce ID and external ID,
  with a modified time and modified-by per record. Supports the calls `SalesforceClient` makes:
  upsert by external ID (Q13), update by ID, and a "changed since T" query paged at 2,000
  records (Q3). *Done 2026-11-27.*
- [x] 4.1.2 Plugs into the real client through `httpx.MockTransport` (built into httpx), so tests
  run our actual `SalesforceClient` code with no network. *Done 2026-11-27.*
- [x] 4.1.3 A pytest fixture that serves the same fake over real HTTP on `localhost` (stdlib
  `ThreadingHTTPServer`), for the one test that needs a separate worker process (5.5). *Done 2026-11-27.*

**Done when:** upserting twice with the same external ID gives one record; the query pages
correctly; the same test passes through the transport and through the local server.

### 4.2 Failure modes
Owner: Phoebe → Ross. Depends on: 4.1.
- [x] 4.2.1 Per-test switches: `down` (connection refused), `slow` (delay), `throttle` (429 with
  `Retry-After`), `error` (5xx), `reject` (400 for a given record), `auth` (401). *Done 2026-11-27.*
- [x] 4.2.2 A call log with timestamps, so tests can assert what was sent and measure the request
  rate. *Done 2026-11-27.*

**Done when:** each mode produces the documented response, and the call log records it.

### 4.3 Scenario builders
Owner: Phoebe → Ross. Depends on: 4.1, 3.1.
- [x] 4.3.1 `src/sync/tests/scenarios.py`: `sales_edit()` changes a record in the fake and returns
  the matching webhook payload — only the changed fields in `changedFields` (Q2, revised
  2026-11-27), with `eventId`, `occurredAt` and `changedBy`. A separate helper builds the
  whole-record fallback case (Q2's "other way") for the one test that needs it. Our own
  writes produce an echo payload with `changedBy` = the integration user. *Done 2026-11-27.*
- [x] 4.3.2 Helpers for a duplicate (same payload twice) and an older event delivered after a newer
  one. *Done 2026-11-27.*
- [x] 4.3.3 Register the pytest marker `brief` for the brief's four checks. *Done 2026-11-27.*

**Done when:** a scenario test posts a sales edit to the webhook endpoint and it appears in the
portal.

---

## Phase 5: Outbound (path B) and the outage

**Goal:** portal edits reach Salesforce under the cap, and survive outages and restarts.
**Exit:** a worker restart while Salesforce is down loses nothing, and the measured rate never exceeds
the configured cap.

### 5.1 `SalesforceClient`
Owner: Phoebe → Ross. Depends on: 1.2, 4.1.
- [x] 5.1.1 An `httpx` client: `upsert(object_type, external_id, fields)` and `update(...)`. *Done 2026-11-27.*
- [x] 5.1.2 Maps responses to outcomes: ok, unreachable, throttled (with `Retry-After`), rejected,
  auth failure. *Done 2026-11-27.*
- [x] 5.1.3 Client tests run against `FakeSalesforce` through `httpx.MockTransport` (4.1.2). *Done 2026-11-27.*

**Done when:** each outcome is mapped correctly against `FakeSalesforce`.

### 5.2 Shared rate limiter
Owner: Phoebe → Ross. Depends on: 5.1.
- [x] 5.2.1 A Redis token bucket (Lua, atomic) at `SALESFORCE_RATE_LIMIT`, shared by every worker
  thread and process, and used for sends, retries, probes and catch-up queries. *Done 2026-11-27.*
- [x] 5.2.2 On a 429: pause until `Retry-After`, halve the rate, then add 5 req/s every 30 s back
  up to the limit. *Done 2026-11-27.*

**Done when:** with the limit at 5 req/s and an injected clock, 100 calls take ≥ 19 s (no
window exceeds the limit); a 429 halves the rate.

### 5.3 Outbound task
Owner: Phoebe → Ross. Depends on: 5.2, 2.3.
- [x] 5.3.1 A Dramatiq actor with `max_retries=0` that claims due rows (`FOR UPDATE SKIP LOCKED`,
  `lease_until`), reads the record's **current** values for the fields, and sends. *Done 2026-11-27.*
- [x] 5.3.2 Outcome handling per design §6: `sent` and store the Salesforce ID; backoff
  1/2/4/8… s capped at 60 s; 429 → slow the limiter down; 400 → `needs_review`; 401/403 →
  stop and alert. *Done 2026-11-27.*
- [x] 5.3.3 Wake-up: a Redis nudge on commit (`transaction.on_commit`), plus a 5-second crontab
  sweep. *Done 2026-11-27.*

**Done when:**
- a portal edit is in the fake within 30 s;
- a 5xx is retried and then sent;
- a 400 goes to `needs_review` and is never dropped;
- a worker killed mid-call loses its lease and the row is sent again.

### 5.4 Down detection and probe
Owner: Phoebe → Ross. Depends on: 5.3.
- [x] 5.4.1 After 5 consecutive unreachable failures, `SyncState` says `down` and sending stops. *Done 2026-11-27.*
- [x] 5.4.2 A probe every 10 s. On success: `up`, trigger catch-up (phase 6), then make every
  pending row due. *Done 2026-11-27.*

**Done when:** with `FakeSalesforce` in `down` mode, the state goes `down` after 5 failures; switched
back to `up`, the state goes `up` within 10 s (injected clock) and the drain starts.

### 5.5 Restart-while-down test (brief check)
Owner: Ross. Depends on: 5.4.
- [x] 5.5.1 With the local-server fake (4.1.3) in `down` mode: make portal edits, run the outbound
  pass in a **separate worker process**, kill it, start a new one, switch the fake to `up`, and
  check every edit arrives. Marked `brief`. *Done 2026-11-27.*

**Done when:** that test passes. This is one of the brief's four demo checks.

---

## Phase 6: Catch-up (path C)

**Goal:** Salesforce changes made during an outage aren't lost.
**Exit:** edits made in `FakeSalesforce` with no webhook sent reach the portal after
recovery.

### 6.1 Catch-up task
Owner: Phoebe → Ross. Depends on: 5.4, 4.1.
- [ ] 6.1.1 Query "changed since watermark − overlap", 2,000 per page, through the limiter.
- [ ] 6.1.2 Each record goes to `apply()` as a Salesforce change: `event_id` = record ID +
  modified time, with a **field-level** echo check.
- [ ] 6.1.3 Advance the watermark only after a page commits.
- [ ] 6.1.4 Schedules: on recovery (before the drain), and every 5 minutes (crontab).

**Done when:**
- 3 sales edits made in the fake without sending webhooks are in the portal after catch-up;
- a second run changes nothing (idempotent);
- catch-up runs before the drain.

---

## Phase 7: Portal API

**Goal:** the endpoints in `openapi.yml` work, as thin generic views over `apply()`.
**Exit:** contract tests per endpoint pass, and the responses match `openapi.yml`.

### 7.1 Customers and contacts
Owner: Phoebe → Ross. Depends on: 2.3, 1.2.
- [ ] 7.1.1 Serializers with explicit fields and camelCase names (as in `openapi.yml`). Add
  `syncState` here: return it from the list/detail serializers (annotation or prefetch, no query
  per row) **and add it back to `SyncFields` in `openapi.yml`**. The contract lists only what the
  API returns today (user decision, 2026-11-27).
- [ ] 7.1.2 `ListCreateAPIView` with cursor pagination and the filters in the contract.
- [ ] 7.1.3 `RetrieveUpdateAPIView`, PATCH only, with `perform_create` / `perform_update` calling
  `apply()`.

**Done when:**
- 201/200/400/401/404 as in the contract;
- a list with 50 rows has a fixed query count;
- a PATCH writes a history row and an outbox row.

### 7.2 History endpoints
Owner: Phoebe → Ross. Depends on: 7.1.
- [ ] 7.2.1 A `ListAPIView` over `FieldChange`, newest first, with the `?field=` filter.

**Done when:** the brief's "who changed the phone, who changed the city" can be answered from
the API, including a superseded row.

### 7.3 Sync status endpoint
Owner: Phoebe → Ross. Depends on: 5.4.
- [ ] 7.3.1 A `RetrieveAPIView` (staff only) returning `SyncStatus`: up/down, pending count, oldest
  pending age, needs-review count, portal edits/s, limiter rate, last catch-up time.

**Done when:** the values match the database in a test, and a non-staff user gets 403.

### 7.4 Generated schema check
Owner: Ross. Depends on: 7.1–7.3.
- [ ] 7.4.1 Generate the schema from the code (`drf-spectacular`) and compare it with
  `docs/openapi.yml`. Any difference is fixed in the code or the contract.

**Done when:** no unexplained differences.

---

## Phase 8: Demo and end-to-end check

**Goal:** one command from a fresh checkout shows the brief's four checks, with a cap low
enough to see.
**Exit:** `bash scripts/demo.sh` passes from a fresh clone.

### 8.1 `scripts/demo.sh`
Owner: Phoebe → Ross. Depends on: phases 3–7.
- [ ] 8.1.1 Start compose (db, redis) and migrate.
- [ ] 8.1.2 Run the scenario tests marked `brief` with `SALESFORCE_RATE_LIMIT=5`, printing ✅/❌ for
  each:
  1. duplicate webhook → one change;
  2. older event after a newer one → current value kept, history has both;
  3. a webhook isn't sent back to Salesforce (0 outbox rows, and the fake's call log is empty);
  4. Salesforce down → portal edits → worker process restarted → Salesforce up → every edit
     arrives.
- [ ] 8.1.3 Print the request rate measured from the fake's call log. It must stay ≤ 5 req/s.

**Done when:** all checks pass twice in a row from a fresh clone.

### 8.2 Outage arithmetic check
Owner: Ross. Depends on: 8.1.
- [ ] 8.2.1 A scaled-down run: N pending edits at limit L should drain in about N ÷ L s. Compare
  with the §10 formula.

**Done when:** the measured time is within 10 % of N ÷ L.

---

## Phase 9: Submission

**Goal:** the three required deliverables are complete and consistent.
**Exit:** the design doc, the chats and the commits match the code.

### 9.1 Design document final pass
Owner: Chandler → Monica. Depends on: phase 8.
- [ ] 9.1.1 Every section in the brief's outline is filled. No TODO except things explicitly left
  out (§12).
- [ ] 9.1.2 Every rule in the doc has a test, and every test is in the doc (Monica cross-checks).
- [ ] 9.1.3 The open-questions list, with one "if the other way" sentence per assumption.

### 9.2 Walkthrough notes
Owner: Chandler. Depends on: 9.1.
- [ ] 9.2.1 `docs/walkthrough.md` in the brief's order, about 10 minutes: assumptions and open
  questions → diagram → rules (conflict, order, loop, outage) → arithmetic.

### 9.3 Chats and commits
Owner: user (`/ready-to-commit`). Depends on: 9.1.
- [ ] 9.3.1 The final export includes subagent chats (1.1.5).
- [ ] 9.3.2 Every phase ends in a commit whose subject states its decision.

**Done when:** the user has reviewed and committed. Pushing is the user's call.

---

## Status

Monica updates this table when a phase starts or finishes. Chandler checks it against the
checkboxes after every task.

| Phase | Status |
|---|---|
| 0 Scaffold | ✅ Done: Django + DRF, uv, compose, `/health/`, agents, `/ready-to-commit`, wiki CI |
| Planning | ✅ Done: architecture, design Q1–Q14, agent team, this plan, apps/models/endpoints, `openapi.yml` |
| 1 Foundations | ✅ Done: 1.1–1.7 all complete (skills, chat export, ledgers T-001, T-002) |
| 2 `apply()` | ✅ Done: `apply()`, `Change`/`Result`, outbox + sync-loop rule built and verified (T-003, 140 tests, 7 `brief`-marked, 15 concurrency re-runs clean) |
| 3 Inbound webhook | ✅ Done: WebhookView, WebhookEventSerializer, token auth, customer linkage, dedupe purge task (T-004, 160 tests, 8 `brief`-marked) |
| 4 Test scenarios | ✅ Done: FakeSalesforce double (fakes.py), MockTransport, failure modes, scenarios (T-005, 171 tests, 9 `brief`-marked) |
| 5 Outbound and outage | ✅ Done: SalesforceClient, Redis limiter, outbound worker, probe, restart test (T-006, 190 tests, 10 `brief`-marked) |
| 6 Catch-up | Not started |
| 7 Portal API | Not started |
| 8 Demo | Not started |
| 9 Submission | Not started |
