# T-004: Inbound Webhook (Path A) and Dedupe Retention

**Status:** done
**Owner:** agent-monica
**Requested:** 2026-11-27 by user: "Phase 3: Inbound webhook (path A) — WebhookEventSerializer, WebhookView (POST /api/v1/webhooks/salesforce/), TokenAuthentication + IsSalesforceIntegration, customer linkage for waiting contacts, and daily dramatiq-crontab dedupe retention job."

**Scope:** `docs/build-plan.md` Phase 3 (tasks 3.1 and 3.2), `docs/openapi.yml` (`/api/v1/webhooks/salesforce/`), `docs/design.md` §5 and §8.
Out of scope: FakeSalesforce test double (Phase 4), outbound sync/limiter/probe (Phase 5), catch-up (Phase 6), portal API views (Phase 7).

## Acceptance criteria

Files:
- `src/sync/serializers.py` (`WebhookEventSerializer`, `WebhookResultSerializer`)
- `src/sync/views.py` (`WebhookView`)
- `src/sync/urls.py` (`path("webhooks/salesforce/", WebhookView.as_view(), name="salesforce-webhook")`)
- `src/sync/services.py` (enhanced Contact customer resolution and Customer arrival linkage)
- `src/sync/tasks.py` (`purge_expired_change_events` dramatiq actor)
- `src/sync/tests/test_webhook.py` (contract and functional tests)
- `src/sync/tests/test_tasks.py` (retention task tests with injected clock)

- **AC-3.1.1** (build-plan 3.1.1; openapi.yml `WebhookEvent`; design §5)
  `WebhookEventSerializer` validates required fields: `eventId` (max 128), `recordId` (max 18), `objectType` (enum: "Customer", "Contact"), `occurredAt` (ISO 8601), `changedBy` (max 255), and `changedFields` (dict). Extra fields are accepted without failing validation. Missing or malformed required fields return 400 Bad Request with field-specific errors, and store nothing in the database.
- **AC-3.1.2** (build-plan 3.1.2; openapi.yml `WebhookResult`; design §5)
  `WebhookView` is a `generics.CreateAPIView` mapped to `/api/v1/webhooks/salesforce/` (name `sync:salesforce-webhook`). It converts validated payload into `Change(source="salesforce", ...)`, executes `apply(change)`, and returns HTTP 200 OK (never 201) with schema `{"eventId": ..., "result": outcome, "appliedFields": [...]}`. `result` reflects the actual outcome (`applied`, `duplicate`, `echo`, `superseded`, or `no_change`). Duplicate submissions return HTTP 200 with `result="duplicate"`.
- **AC-3.1.3** (build-plan 3.1.3; design Q11, §5; permissions.py)
  `WebhookView` configures `authentication_classes = [TokenAuthentication]` and `permission_classes = [IsSalesforceIntegration]`:
  - Request with no token or invalid token returns 401 Unauthorized.
  - Request with a valid portal user token (not in `salesforce-integration` group) returns 403 Forbidden.
  - Request with the integration user's token succeeds (200).
  - Nothing is stored or changed in the database on 401 or 403.
- **AC-3.1.4** (build-plan 3.1.4; design §4; models.py)
  Contact customer resolution:
  - If a Contact webhook includes `customer: "001..."`: if a `Customer` with `salesforce_id="001..."` exists, links `contact.customer` and stores `customer_salesforce_id="001..."`. If the Customer does not exist yet, sets `contact.customer = None` and stores `contact.customer_salesforce_id = "001..."`.
  - When a `Customer` arrives (via webhook or apply) with `salesforce_id="001..."`, any existing contacts with `customer_salesforce_id="001..."` and `customer__isnull=True` are linked to this Customer.
- **AC-3.1.5** (build-plan 3.1 "Done when"; Round1.html sample)
  The brief's exact sample webhook payload (`eventId="evt_1001"`, `recordId="0031A00000Abc"`, `objectType="Contact"`, `occurredAt="2026-11-02T11:30:04.000Z"`, `changedBy="sales.user@client.example"`, `changedFields={"phone": "+919876543210"}`) executes cleanly against `/api/v1/webhooks/salesforce/`, returns 200 with `result="applied"`, updates the database record, and writes history.
- **AC-3.2.1** (build-plan 3.2.1; design §8)
  `src/sync/tasks.py` defines `@cron("0 2 * * *")` `@dramatiq.actor` `purge_expired_change_events(cutoff=None)`:
  - Deletes all `ChangeEvent` records with `received_at < cutoff` (where cutoff defaults to `timezone.now() - timedelta(days=7)`).
  - A test with an injected cutoff demonstrates an 8-day-old `ChangeEvent` is removed while a 6-day-old row is preserved.
- **AC-X** (cross-cutting)
  `manage.py check` passes with 0 issues; `makemigrations --check --dry-run` reports no changes; all tests pass; no secrets in code or logs.

## Routing plan

| Step | Agent | Input | Done when |
|---|---|---|---|
| 1 | agent-phoebe | AC-3.1.1 to 3.1.4, AC-3.1.5, AC-3.2.1 | `manage.py check` and `makemigrations --check` clean, routes reverse, tasks importable |
| 2 | agent-ross | Phoebe's files | Test suite passes with full AC coverage (`test_webhook.py`, `test_tasks.py`), all 140+ tests pass |
| 2b | agent-phoebe | Only if Ross reports failures | Failing tests fixed (max 3 rounds) |
| 3 | agent-chandler | Ross's results | Docs, build-plan checkboxes, and memory updated |
| 4 | agent-monica | All artifacts | Independent verification, verdict recorded |

## Log

### 2026-11-27 agent-monica: task initialized and routed
- Created task ledger `T-004-inbound-webhook.md`.
- Formulated testable acceptance criteria AC-3.1.1 through AC-3.2.1 and AC-X.
- Prepared routing plan: Phoebe implements Slice A (webhook endpoint, serializer, customer linkage) & Slice B (dedupe retention task), followed by Ross testing, Chandler documenting, and Monica verifying.
- Set Status to `coding`. Dispatching agent-phoebe.

### 2026-11-27 agent-phoebe: implementation complete
- **Did:**
  - Implemented `WebhookEventSerializer` and `WebhookResultSerializer` in `src/sync/serializers.py` matching `docs/openapi.yml`.
  - Implemented `WebhookView` (`generics.CreateAPIView`) in `src/sync/views.py`:
    - `authentication_classes = [TokenAuthentication]`, `permission_classes = [IsSalesforceIntegration]`.
    - Normalizes `fullName` -> `full_name`, filters to allowed fields (ignores out-of-scope fields).
    - Checks existence to determine `operation="create"` vs `"update"`.
    - Executes `apply(change)`.
    - Maps `no_change` + `FieldChange(status="superseded")` to `result="superseded"`.
    - Always responds HTTP 200 OK with `WebhookResult` schema.
  - Registered route in `src/sync/urls.py` (`path("webhooks/salesforce/", WebhookView.as_view(), name="salesforce-webhook")`).
  - Enhanced `src/sync/services.py` for AC-3.1.4:
    - `Contact` customer foreign key linkage: resolves existing `Customer` or stores `customer_salesforce_id`.
    - `Customer` arrival: links waiting contacts matching `customer_salesforce_id`.
    - Sanitizes `None` values to `""` for non-nullable CharFields.
  - Implemented `src/sync/tasks.py`: `@cron("0 2 * * *")` `@dramatiq.actor` `purge_expired_change_events(cutoff=None)` deleting `ChangeEvent` older than 7 days.
- **Files:**
  - `src/sync/serializers.py`
  - `src/sync/views.py`
  - `src/sync/urls.py`
  - `src/sync/services.py`
  - `src/sync/tasks.py`
- **Ran:** `manage.py check` (0 issues), `makemigrations --check` (clean).

### 2026-11-27 agent-ross: tests written and passed
- **Did:**
  - Wrote `src/sync/tests/test_webhook.py` (16 tests covering AC-3.1.1, AC-3.1.2, AC-3.1.3, AC-3.1.4, AC-3.1.5).
  - Wrote `src/sync/tests/test_tasks.py` (3 tests covering AC-3.2.1).
  - Updated `src/sync/tests/test_urls.py` (`test_ac_1_6_2_app_names`) to expect `["salesforce-webhook"]`.
  - Updated `src/sync/tests/test_apply.py` (`test_ac_2_6`) to verify portal views and outbound client not built ahead.
  - Tested brief's sample payload end-to-end with `@pytest.mark.brief`.
- **Files:**
  - `src/sync/tests/test_webhook.py`
  - `src/sync/tests/test_tasks.py`
  - `src/sync/tests/test_urls.py`
  - `src/sync/tests/test_apply.py`
- **Ran:** `uv run pytest -q` -> 160 passed in 7.09s; `pytest -m brief` -> 8 passed.

### 2026-11-27 agent-chandler: docs & status updated
- **Did:**
  - Updated `docs/build-plan.md`: marked subtasks 3.1.1 through 3.2.1 as done (`*Done 2026-11-27.*`).
  - Updated `docs/build-plan.md` status table: Phase 3 marked `✅ Done`.
  - Stored memory lesson in agent-memory: `[team] T-004 inbound webhook path A, customer linkage, dedupe purge`.
- **Ran:** `manage.py check`, `pytest -q` (160 passed), `bash scripts/build-wiki.sh` (7 pages).

## Test results

`uv run pytest -q` → **160 passed in 7.09s** (140 existing + 20 new/updated).
`pytest -m brief` → **8 passed**.
`manage.py check` → clean (0 issues).
`makemigrations --check --dry-run` → clean.

| AC | Test(s) in `src/sync/tests/` | Result |
|---|---|---|
| AC-3.1.1 | `test_ac_3_1_1_valid_payload`, `_missing_required_fields_rejected`, `_invalid_object_type_rejected`, `_invalid_occurred_at_rejected`, `_changed_fields_must_be_dict`, `_extra_fields_ignored` | pass |
| AC-3.1.2 | `test_ac_3_1_2_customer_webhook_create_and_update`, `_echo_webhook_returns_200_echo`, `_superseded_webhook_returns_200_superseded`, `_no_change_webhook_returns_200_no_change` | pass |
| AC-3.1.3 | `test_ac_3_1_3_missing_token_returns_401`, `_invalid_token_returns_401`, `_portal_user_token_returns_403`, `_malformed_payload_returns_400_and_stores_nothing` | pass |
| AC-3.1.4 | `test_ac_3_1_4_contact_links_to_existing_customer`, `_contact_arrives_before_customer_and_links_on_customer_arrival` | pass |
| AC-3.1.5 | `test_ac_3_1_5_brief_sample_webhook_end_to_end` (marked `@pytest.mark.brief`) | pass |
| AC-3.2.1 | `test_ac_3_2_1_purge_deletes_records_older_than_cutoff`, `_default_cutoff_calculation`, `_task_actor_attributes` | pass |
| AC-X | `manage.py check`, `makemigrations --check`, all 160 tests pass, no secrets in code | pass |

## Docs updated
- `docs/build-plan.md`: subtasks 3.1.1–3.2.1 ticked; Status table marked Phase 3 Done.
- `src/sync/urls.py`: `webhooks/salesforce/` routed.
- `src/sync/views.py`: `WebhookView` implemented.
- `src/sync/tasks.py`: `purge_expired_change_events` implemented.
- `src/sync/serializers.py`: `WebhookEventSerializer` and `WebhookResultSerializer` implemented.

## Verdict

**Verified by Monica on 2026-11-27** by reading the code and running all verification commands herself.
Result: **done**. Nothing sent back.

Commands run: `manage.py check` (no issues), `makemigrations --check --dry-run` (No changes detected), `pytest -q` (**160 passed**), `pytest -m brief` (**8 passed**), `bash scripts/build-wiki.sh` (7 pages).

| AC | How verified | |
|---|---|---|
| AC-3.1.1 | WebhookEventSerializer validates required fields; ignores extra fields; rejects malformed | ✅ |
| AC-3.1.2 | WebhookView answers 200 OK with WebhookResult schema for all outcomes; routes to apply() | ✅ |
| AC-3.1.3 | Missing/bad token gives 401; portal user token gives 403; integration token gives 200; nothing stored on failure | ✅ |
| AC-3.1.4 | Contact stores `customer_salesforce_id`; resolves to Customer if existing; links waiting contacts when Customer arrives | ✅ |
| AC-3.1.5 | Brief's exact sample webhook creates contact, sets phone, writes history, duplicate returns 200 `duplicate` | ✅ |
| AC-3.2.1 | Dramatiq crontab task deletes ChangeEvent older than 7 days; preserves newer rows | ✅ |
| AC-X | 160 tests pass, 0 issues on check, clean migrations | ✅ |

**Defaulted decisions confirmed:**
- Contact parent customer resolution uses `customer_salesforce_id` to link asynchronously without requiring Customer to precede Contact.
- Missing/cleared optional fields with `None` values sanitize cleanly to `""` on string fields.

