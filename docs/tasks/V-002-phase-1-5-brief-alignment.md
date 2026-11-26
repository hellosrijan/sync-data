# V-002: Brief-alignment audit, phases 1-5 (re-audit + phase 5 added)

**Status:** reviewing
**Owner:** agent-monica
**Requested:** 2026-11-26 by the user: "Now i want each agent to pick each phase and test it
till phase 5. At the end give me the report of the testing. Don't run pipenv just find out that
the implementation is as per the requirement we have or not."

**Purpose:** a fresh, independent read of phases 1-5 against `docs/Round1.html` itself. This
extends `docs/tasks/V-001-phase-1-4-brief-alignment.md` (which covered only phases 1-4) to
include phase 5, and re-checks phases 1-4 since V-001's one shipped fix (below). Five
`agent-rachel` instances, one per phase, dispatched concurrently. See
`.claude/skills/brief-alignment-review/SKILL.md` for their procedure, and
`.claude/rules/agent-team.md` ("Brief-alignment audits").

**Per the user's explicit instruction this round: reviewers do not run `pytest`, `uv run`, or any
package-manager command.** Verify by reading `docs/Round1.html`, the actual code, and the
relevant test files directly (read, don't execute) to judge whether a requirement is covered —
not by running the suite. This also avoids the shared-test-DB collisions V-001 hit when four
reviewers ran pytest concurrently (see V-001's "Process note").

**Known context from V-001 (read before starting your phase):**
- Item 1 (phase 2, critical): echo branch never updated `field_meta[f].sf_value` — **fixed** in
  `docs/tasks/T-007-echo-sf-value-fix.md` (shipped in `src/sync/services.py:56-64`, tests added,
  docs updated). The phase 2 reviewer should confirm this is actually fixed by reading the code
  (not just trusting this note), not re-report it as open.
- Item 2 (phase 1, critical, still **open**, no fix yet): `CustomerAdmin`/`ContactAdmin` define no
  permission overrides, so Django's defaults let staff bypass `apply()` via the admin's own
  `Model.save()`. The phase 1 reviewer should re-confirm this is still the case.
- Items 3-7 (important) and 8-12 (minor) from V-001 are still open/unfiled. If your phase's
  reviewer independently finds the same gap, say so and reference the V-001 item number rather
  than treating it as new; if you find something V-001 missed, it's a new finding.

**Phase scope (from `docs/build-plan.md`):**
- Phase 1 — Foundations: models, admin, auth, `api/v1/` URL layout (ledgers T-001, T-002)
- Phase 2 — `apply()`: the rules core (ledgers T-003, T-007)
- Phase 3 — Inbound webhook: `WebhookView`, token auth, customer linkage (ledger T-004)
- Phase 4 — Test scenarios: `FakeSalesforce`, `MockTransport`, scenario builders (ledger T-005)
- Phase 5 — Outbound (path B) and the outage: `client.py`, `limiter.py`, `tasks.py` (send_outbound,
  probe_salesforce, recover_limiter_rate, sweep_outbound) (ledger T-006) — **not reviewed before**

## Log

### 2026-11-26 agent-monica: audit set up, dispatching five reviewers
- Re-established ground truth: `src/sync/services.py` has the T-007 echo fix; 192 tests exist;
  build-plan phases 0-5 marked done, 6-9 not started.
- Dispatching agent-rachel × 5, one per phase (1, 2, 3, 4, 5), concurrently, each reading-only
  (no pytest/uv commands this round, per the user's instruction).

## Findings
<!-- One section per phase, appended by that phase's agent-rachel. Never edit another phase's section. -->

## Phase 4: Test scenarios (a fake Salesforce inside the tests) — reviewed by agent-rachel

**Verified independently (read-only, no tests run):** read `docs/Round1.html` (full, lines 609-651),
`docs/tasks/T-005-fake-salesforce-scenarios.md`, `docs/build-plan.md` §4 (lines 346-386),
`docs/design.md` (Q2, Q3, Q4 rows and §6/§10 references), `pyproject.toml` (line 28, `brief`
marker), `git diff -- src/sync/tests/fakes.py` (uncommitted change, 5 lines), and in full
`src/sync/tests/fakes.py` (231 lines), `src/sync/tests/scenarios.py` (75 lines) and
`src/sync/tests/test_fakes.py` (289 lines). Did not run pytest, `manage.py check`, `makemigrations`,
or any uv command. Results below are by reading only.

### Findings
Note: `git status` shows `src/sync/tests/fakes.py` modified and uncommitted. The T-005 ledger's
171-pass result was recorded before that change, so the 171-pass claim does not describe the
current working tree.

- **[severity: important]** The HTTP query path raises `NameError` when more than 2,000 records match.
  - Brief says (`docs/Round1.html`): "Drain the backlog as fast as the rate limit allows."
  - Code: `src/sync/tests/fakes.py:172` calls `quote(q_str)`. The import on `fakes.py:8` is
    `from urllib.parse import parse_qs, unquote, urlparse`, with no `quote`. Nothing else in the
    module defines it. The uncommitted diff added the `quote` call without the import.
  - Gap: any `GET /query/` that is not `done` (more than the 2,000 default page size) fails with
    `NameError` before the response is sent. No test reaches this path. `test_ac_4_1_1_changed_since_query_paging`
    (`test_fakes.py:97-118`) calls `changed_since` directly, and `test_ac_4_1_2_mock_transport_query`
    (`test_fakes.py:151-162`) returns one record, so neither reaches it. The catch-up paging the brief needs
    is therefore unexercised over HTTP.
  - Suggested owner: Phoebe (add the import). Ross (add an HTTP paging test that walks `nextRecordsUrl`
    to the last page, through both transport and live server).

- **[severity: important]** MockTransport logs every non-down request twice, so `fake.calls` over-counts
  (confirms V-001 item 6, and it is worse than V-001 described).
  - Brief says (`docs/Round1.html`): "The CRM API allows at most <strong>100 requests per second</strong>."
    The call log is how AC-4.2.2 measures the rate against that cap.
  - Code: `src/sync/tests/fakes.py:181` (`fake.calls.append(...)` in the `handler`) and `fakes.py:102`
    (`fake.calls.append(...)` inside `_handle_request`, which the handler then calls at `fakes.py:184`).
    So a MockTransport request that is not `down` is logged twice. A `down` request is logged once, because
    the handler raises at `fakes.py:182-183` before `_handle_request`. The live server path logs once
    (`_handle_request` only, via `_dispatch` at `fakes.py:210`). The two paths disagree, as V-001 item 6 said.
  - Gap: `test_ac_4_2_2_call_log_records_timestamps_and_methods` (`test_fakes.py:225-239`) sends two
    requests and asserts `len(fake.calls) == 2`. By reading, the current code gives 4, so this test fails
    against the working tree. The double count would also double any measured request rate. The T-005 pass
    claim predates this uncommitted change.
  - Suggested owner: Phoebe (log in one place only, in `_handle_request`, and remove `fakes.py:181`), then
    Ross (re-run `test_ac_4_2_2` and confirm the count against the fixed code).

- **[severity: important]** The done-when for 4.1 and 4.2 is not met on the live-server path.
  - Build-plan 4.1 done-when (`docs/build-plan.md` line ~364): "the same test passes through the transport
    and through the local server"; 4.2 done-when: "each mode produces the documented response, and the call
    log records it."
  - Code: `test_fakes.py:169-181` is the only live-server test, and it sends one upsert-create. The failure
    modes are tested only through `mock_client` (`test_fakes.py:188-222`). The query path is not tested over HTTP
    at all (see the first finding). There is no live-server `down`, `throttle`, `reject`, or `auth` test.
  - Gap: the local server is the path phase 5.5 relies on for a separate worker process. Its failure modes and
    paging are unverified.
  - Suggested owner: Ross (parametrize the failure-mode and paging tests over both fixtures).

- **[severity: important]** The paging contract is not Salesforce's, so the fake is not a reliable stand-in for
  `nextRecordsUrl` (V-001 item 11, restated).
  - Brief says (`docs/Round1.html`): "Drain the backlog as fast as the rate limit allows." and "Pending calls
    to Salesforce must still be there after our own process restarts." The catch-up in phase 6 depends on
    paging through changed records (design Q3: "a query for records changed since time T, returning about 2,000
    records per call").
  - Code: `fakes.py:172` (as written, with the missing import). At HEAD (`git diff` shows the removed line),
    `nextRecordsUrl` was `/services/data/v60.0/query/?offset=N` with no filter, which V-001 item 11 described.
    The working-tree version keeps the filter but adds an `offset` query parameter. Real Salesforce returns an
    opaque cursor URL in `nextRecordsUrl` (outside the brief; external knowledge), and clients must follow it
    without building their own offsets.
  - Gap: a client written against the fake's `offset=` scheme could pass the tests and fail on the real API. The
    fake accepts that. Phase 5 `client.py` and phase 6 catch-up must follow `nextRecordsUrl` as an opaque URL.
  - Suggested owner: Phoebe (make the fake's `nextRecordsUrl` opaque, e.g. a cursor token, and document the
    assumption in `design.md` §1). Re-check when phase 5 `client.py` is audited.

- **[severity: minor]** The `our_echo` test is a tautology (V-001 item 7, confirmed).
  - Brief says (`docs/Round1.html`): "An update we send to Salesforce must not bounce back and forth forever."
    The test exists to exercise that loop rule through the echo payload.
  - Code: `src/sync/tests/test_fakes.py:262`: `assert "settings" in echo_payload["changedBy"] or echo_payload["changedBy"]`.
    The right-hand side is truthy for any non-empty string. `scenarios.py:50,56` set `changedBy` from
    `settings.SALESFORCE_INTEGRATION_USER`, but the test never compares it to `settings`.
  - Gap: the test cannot fail for a wrong actor. The `our_echo` builder is effectively untested.
  - Suggested owner: Ross (assert `== settings.SALESFORCE_INTEGRATION_USER`).

- **[severity: minor]** Upsert response status differs from Salesforce's documented behaviour.
  - Code: `fakes.py:126` returns 200 with a body on update. `test_fakes.py:135-138` asserts it.
  - Gap: Salesforce's sObject upsert returns 201 on create and 204 No Content on update (external knowledge,
    outside the brief). A client that branches on status would behave differently on the real API.
  - Suggested owner: Phoebe (fidelity), low priority.

- **[severity: minor]** The brief-marked test covers only the happy path.
  - Brief says (`docs/Round1.html`): "A duplicate, an older event after a newer one, and a restart with the fake
    CRM down are the likely changes."
  - Code: `test_fakes.py:276-289` posts only `sales_edit` once. `duplicate` and `stale_after` are built
    (`scenarios.py:61-75`) but never posted to `/api/v1/webhooks/salesforce/` in this file.
  - Gap: this phase's `brief` marker does not exercise the duplicate or out-of-order rules. Whether phases 2 and 3
    cover them is for those phases' audits. Scope note only; the helpers themselves match the brief's sample payload
    fields (`eventId`, `recordId`, `objectType`, `occurredAt`, `changedBy`, `changedFields`).
  - Suggested owner: Ross (post `duplicate` and `stale_after` through the webhook under the `brief` marker).

- **[severity: minor]** Relies on `django.utils.timezone` re-exporting stdlib names.
  - Code: `fakes.py:146` (`timezone.timedelta`), `test_fakes.py:235` (`timezone.datetime`).
  - Gap: works today because Django's `timezone` module imports them, but it is fragile. Use `datetime`/`timedelta`
    imports instead.
  - Suggested owner: Phoebe (polish).

### Checked and found matching (no gap)
- Scenario payloads carry the brief sample's fields (`scenarios.py:17-24`, `34-41`, `51-58`), and `sales_edit`
  sends only changed fields (Q2 default), with `sales_edit_whole_record` as the fallback.
- Failure modes exist for `down`, `slow`, `throttle` (429, `Retry-After: 1`), `error` (500), `reject` (400),
  and `auth` (401) (`fakes.py:104-124`). `down` raises `httpx.ConnectError` on the transport path and returns 503 on
  the live path, which the AC allows.
- Upsert by external ID is idempotent with one record (`fakes.py:43-62`, `test_fakes.py:62-75`).
- `brief` marker registered (`pyproject.toml:28`).

### Scope note
- Phase 5 (`client.py`) must treat `nextRecordsUrl` as opaque. Re-check that in the phase 5 review.
- The fake does not enforce the 100 req/s cap. That is the limiter's job (phase 5), so this is not a phase 4 gap.
- Ledger status: T-005 is marked done with 171 passing tests, but the current uncommitted `fakes.py` change was not
  in that run. Monica should treat T-005's pass claim as stale until Ross re-runs it.

## Phase 1: Foundations — reviewed by agent-rachel

**Verified independently (read-only, no tests run):** read `docs/Round1.html` (Background, Systems,
Requirements and scale, Constraints, Deliver, Assumptions; roughly lines 610-650); `docs/build-plan.md`
lines 100-240 (Models table, phase 1 tasks 1.1-1.7 and their "Done when"); in full `src/sync/models.py`,
`src/sync/admin.py`, `src/sync/permissions.py`, `src/sync/serializers.py`, `src/sync/urls.py`,
`src/config/urls.py`, `src/utils/urls.py`, `src/utils/serializers.py`,
`src/sync/management/commands/create_integration_user.py`; `src/config/settings.py` (lines 39-55, 142-154);
`src/sync/tests/test_admin.py` (lines 110-161), `src/sync/tests/test_auth.py` (lines 90-112); ledgers
`T-001`, `T-002`; and V-001's header and Log. Grep `api/v1` over `src/` excluding tests returns only
`src/config/urls.py:23`.

### Findings

- **[severity: critical]** Django admin lets staff write Customer and Contact without `apply()`.
  This is V-001 item 2, re-confirmed still open.
  - Brief says (`docs/Round1.html`): "The earlier values, who made them, and when, stay on record.
    Dropping that history counts as losing a change." and "Both sides may create. Both sides may update."
  - Code: `src/sync/admin.py:20-26` (`CustomerAdmin`) and `src/sync/admin.py:29-37` (`ContactAdmin`)
    define no `has_add_permission`, `has_change_permission`, `has_delete_permission` or `save_model`
    override. Django's defaults apply, so any staff user with the model's add/change/delete permissions
    can save through the admin ModelForm, which is a plain `Model.save()`. That writes no `FieldChange`
    row, sets no `field_meta`, and creates no `Outbox` row, so the edit never reaches Salesforce.
  - Gap: the admin change form is not limited to display fields. `ModelAdmin` shows every editable field,
    and `readonly_fields` (`admin.py:23`, `admin.py:32`) covers only the audit columns and timestamps. So an
    admin user can edit `salesforce_id` (which breaks the link to the Salesforce record), `external_id`, and
    `field_meta` (the per-field `at`/`source`/`by`/`sf_value` that the ordering and echo rules read). An admin
    add creates a Customer or Contact with no outbound create and no history. Admin deletes are out of the
    brief's scope, but they still skip Salesforce.
  - Tests: `src/sync/tests/test_admin.py:117-133` checks only that `FieldChange`/`ChangeEvent` refuse writes.
    Nothing checks write refusal for Customer or Contact, or the editable fields, so a regression here would
    not be caught.
  - Note for Monica: the brief does not literally forbid admin writes. The critical rating comes from the
    brief's "dropping that history counts as losing a change" and from our own `.claude/rules/drf.md` rule
    ("never write them with `Model.save()`"). Monica can decide whether to keep it critical or make it
    important; I rate it critical, the same as V-001.
  - Suggested owner: Phoebe (code fix: make Customer and Contact read-only in admin, or route saves through
    `apply()`, and make `salesforce_id`, `external_id` and `field_meta` read-only); then Ross (missing test:
    staff POST to the Customer/Contact add and change pages must be refused or routed).

- **[severity: minor]** The requeue action is not gated by a change permission.
  - Brief says: nothing directly. `.claude/rules/drf.md` ("Django admin") and build-plan 1.4.3 say the outbox
    is read-only except the requeue action for `needs_review` rows.
  - Code: `src/sync/admin.py:52-57` (`OutboxAdmin.actions = ["requeue"]`) and `admin.py:59-60`
    (`@admin.action` with no `allowed_permissions`). `ReadOnlyAdmin` (`admin.py:13-14`) returns False for change,
    but Django checks an action's permission only when the action sets `allowed_permissions`. So a staff user
    with view permission on `Outbox` can run requeue. I read this from Django's `ModelAdmin` action filtering;
    I did not run it, because no tests were run this round.
  - Gap: requeue writes rows, but it is gated by view permission, not change permission.
  - Suggested owner: Phoebe (set `allowed_permissions = ("change",)` on the action); then Ross (test that a
    view-only user cannot requeue).

- **[severity: minor]** `create_integration_user` prints the existing token on every re-run, not once.
  - Brief says: nothing (token handling is our decision, design Q11).
  - Our build plan says: `docs/build-plan.md` 1.2.3 "a `create_integration_user` command that prints the token once".
  - Code: `src/sync/management/commands/create_integration_user.py:22-23` fetches the existing `Token` and writes
    `token.key` to stdout on every call.
  - Gap: the re-run is idempotent for the user, group and token (`test_auth.py:105-112`), but the token is printed
    again each time. The build plan's "prints once" is not true of the code, and no test covers it. Printing a live
    secret to stdout on each run adds to log-leak risk.
  - Suggested owner: user (decide print-once vs print-always); then Phoebe (code) or Chandler (doc-only, if
    print-always is kept).

### Checked and held

- All seven models exist with the brief's fields (`models.py:35-58`): Customer name, status, owner; Contact
  full_name, phone, email, city, owner, customer. No extra synced fields, as the brief asks.
- Uniqueness and indexes match build-plan 1.3 and the brief's constraints: `external_id` and `salesforce_id` unique
  on `SyncRecord` (`models.py:16-17`); `ChangeEvent.event_id` DB-unique (`models.py:71`); `Outbox` partial unique on
  one pending row per record (`models.py:138-145`); indexes on `(state, next_attempt_at)`,
  `(object_type, record_id, occurred_at)`, `received_at`, Contact `email`, `customer_salesforce_id`, and the FK
  `customer`. These support lookups at 5M rows.
- `Outbox.fields` is an `ArrayField` of field names (`models.py:124`), so the outbound call can send only changed fields.
- `Contact.customer` is nullable with `PROTECT` (`models.py:55`), and `customer_salesforce_id` is stored so a contact
  can arrive before its customer.
- Auth: `DEFAULT_AUTHENTICATION_CLASSES` is Token only and `DEFAULT_PERMISSION_CLASSES` is IsAuthenticated
  (`settings.py:142-146`). `HealthView` is AllowAny (`src/utils/views.py:14-15`). Health is at `api/v1/health/`
  (`src/utils/urls.py:8`), and the old root `/health/` is gone (`src/config/urls.py:21-23`).
- `IsSalesforceIntegration` requires authentication and group membership (`permissions.py:6-10`), so a portal
  user without the group is denied.
- URL layout: `api/v1/` is defined once, in `src/config/urls.py:23`. `utils` is included before `sync`, and both
  apps have `app_name` and `urls.py`. Resource names are singular.
- Serializers: explicit `fields` (no `__all__`); `id`, the sync columns and the audit columns are read-only;
  `FieldChangeSerializer` is fully read-only (`serializers.py:9-41`). Field names match the brief.
- Admin: every model is registered. Append-only models refuse add, change and delete (`ReadOnlyAdmin`).
  `Contact` uses `raw_id_fields` and `list_select_related`. Search fields are indexed.
- The brief's numbers: nothing in phase 1 breaks 5M records, 100 req/s or 30 s. Phase 1 adds no call path to Salesforce.

### Scope note

- `src/sync/serializers.py:25-31` (`ContactSerializer`) exposes `customer` as a writable PK field. Fine for reads now.
  Phase 7 must route every portal write through `apply()` (drf.md), not `serializer.save()`, or the bypass in the
  critical finding returns through the API.
- `src/config/settings.py:153` sets `SALESFORCE_INTEGRATION_USER` to a DEFAULTED placeholder
  (`integration-user@example.invalid`). The echo rule (design §9) depends on this name, so it belongs to phase 2
  (echo) and phase 5 (outbound). Those phases need to check it, and the user should confirm the real value.
- Webhook auth (Q11: `TokenAuthentication` plus the integration group) is phase 3 scope, not phase 1.

## Phase 3: Inbound webhook (path A) — reviewed by agent-rachel

**Verified independently (read-only, no tests run):** read `docs/Round1.html` (the Webhooks, Sync,
Constraints and "A webhook is a message" passages), `docs/build-plan.md` 3.1-3.2, `docs/design.md` §5 and
§8, `docs/openapi.yml` `WebhookEvent`, `docs/tasks/T-004-inbound-webhook.md`, and the code
`src/sync/views.py`, `serializers.py`, `permissions.py`, `urls.py`, `services.py` (apply and
`_lock_record`, `_apply_field`), `tasks.py` (`purge_expired_change_events`, `_handle_outcome`),
`models.py`, `src/config/settings.py` (REST_FRAMEWORK, TIME_ZONE), migration `0001_initial.py`, and
the test names in `test_webhook.py` / `test_tasks.py`. Did not execute `check`, `makemigrations`,
`pytest` or `-m brief`; the test bodies were not read beyond the linkage tests.

### Findings

Holds up on reading (no finding): duplicate webhook (`services.py:49-52` ChangeEvent insert in a
savepoint, unique constraint `models.py:71`, returns `duplicate`); out-of-order (`services.py:135-142`
compares `occurred_at` against the stored `at`, and an older event becomes `superseded` at `services.py:69`);
auth (`settings.py:144-145`, view `views.py:25-26`, `TokenAuthentication` first; `IsSalesforceIntegration`
extends `IsAuthenticated` and checks the `salesforce-integration` group at `permissions.py:10`; DRF runs
permission checks before the body is parsed, so 401/403 store nothing); retention (`tasks.py:17,68-81`
keeps 7 days, longer than the 6-hour outage, and the purge is batched); indexed lookups on the webhook
path (`views.py:51` uses the unique `salesforce_id`, `models.py:58` indexes `customer_salesforce_id`,
`models.py:68` indexes `received_at`).

- **[severity: important]** The webhook accepts any value type and length in `changedFields`, so bad
  values reach `apply()` and fail as an unhandled 500, or store garbage. This is V-001 item 4,
  re-confirmed.
  - Brief says (`docs/Round1.html`): "A webhook is a message. Our endpoint has to accept it, check it,
    and apply it before anything is stored."
  - Code: `src/sync/serializers.py:50` `changedFields = serializers.DictField(required=True)` has no
    child validator; `serializers.py:52-55` `validate_changedFields` only checks `isinstance(value, dict)`.
    `views.py:44-47` copies the values unchanged into `Change.fields`. `services.py:151` stores
    `str(value)`, so `{"x": 1}` is written as `"{'x': 1}"`.
  - Gap: `models.py:58` `customer_salesforce_id` is `max_length=18`, and `models.py:51` `phone` is 40.
    A `customer` value longer than 18 characters, or a `phone` longer than 40, reaches Postgres as an
    over-length `varchar` and raises `DataError`. Nothing catches it, so the response is a 500, not a 400.
    `apply()`'s atomic block rolls back, so nothing is stored, but the 400-on-malformed rule in design §5
    step 3 is not met for these inputs. The OpenAPI contract (`openapi.yml` `WebhookEvent.changedFields`)
    types these members as `string`, and the code does not enforce that. No test in `test_webhook.py`
    covers value types or lengths (test names at lines 65-155).
  - Suggested owner: Phoebe (serializer: per-field types and max lengths for in-scope fields, 400 on
    failure). Ross (value-type and over-length tests).

- **[severity: important]** The superseded-vs-no_change check runs an unindexed query on the webhook
  path. This is V-001 item 5, re-confirmed.
  - Brief says (`docs/Round1.html`): "5 million records in production, customers and contacts counted
    together." and "Salesforce may deliver the same webhook twice."
  - Code: `src/sync/views.py:66-69` runs `FieldChange.objects.filter(event_id=event_id,
    status=SUPERSEDED).exists()` on every `no_change` result. `src/sync/models.py:95` declares
    `event_id` with no `db_index`. The only index on FieldChange is `field_change_record_idx` on
    `(object_type, record_id, occurred_at)` (`models.py:99-101`). Migration `0001_initial.py:155` has no
    index on it either.
  - Gap: FieldChange is append-only and never purged, and it gets a row for every applied, superseded and
    echo field change (`services.py:58`, `:70`). At 5M records it reaches tens of millions of rows, so this
    is a sequential scan inside the webhook request. That works against the 30 s target. The query is also
    unnecessary, because `apply()` already knows the outcome.
  - Suggested owner: Phoebe. Preferably have `apply()` return a superseded flag in `Result` and drop the
    query. The alternative is a migration adding `db_index=True` to `event_id`. Ross: a test that pins the
    `superseded` mapping after the change.

- **[severity: important]** An echo of our own update for a record we do not hold yet returns an unhandled
  500.
  - Brief says (`docs/Round1.html`): "An update we send to Salesforce must not bounce back and forth forever."
  - Code: `services.py:114-119` in `_lock_record`: for an echo the view sends `operation="create"` (computed
    at `views.py:51` when no local row exists), so the create branch is skipped and `qs.get(salesforce_id=...)`
    raises `DoesNotExist`, which is re-raised as `UnknownRecord`. Nothing catches `UnknownRecord` (grep finds
    it only in `services.py` and the tests), and `settings.py` sets no `EXCEPTION_HANDLER`. `views.py:63` calls
    `apply()` without a try.
  - Gap: the only place a Salesforce ID is stored for a portal-created row is `tasks.py:197-199`, after the
    Salesforce response. A crash between that response and the save leaves the row without its ID, and the
    echo then gets a 500. The brief does not say how Salesforce treats a 500 on a webhook, so the sync-loop
    rule has no defined outcome for this input. Rated important, not critical, because it needs a crash
    window. Monica may escalate it, since it touches one of the four checked rules.
  - Suggested owner: Phoebe (catch `UnknownRecord` in the view or in `apply()` for echoes, and return 200
    with a defined outcome; the user decides the outcome if the design does not fix it). Ross: a test for an
    echo on an unknown `recordId`.

- **[severity: minor]** A Customer that gets its Salesforce ID from the outbound path does not link waiting
  contacts. This is a gap in the design's linkage rule, not in AC-3.1.4 as written.
  - Brief says (`docs/Round1.html`): "The portal should show that customer."
  - Code: `tasks.py:197-199` sets `record.salesforce_id` and saves, with no contact linkage. Linkage exists only
    in `services.py:81-86`, which runs inside `apply()`.
  - Gap: `design.md` §5 step 4 and AC-3.1.4 say "via webhook or apply()". The sequence "Salesforce creates the
    customer, its contact webhook arrives before our outbound response is stored" leaves the contact with
    `customer=None` until the next applied edit to that Customer. Decide whether an outbound-assigned ID counts
    as "arrival". If it does, this is a real gap.
  - Suggested owner: user (decide whether the rule covers outbound-assigned IDs). If yes, Phoebe (phase 5 code,
    `tasks.py`).

- **[severity: minor]** The contact linkage in `apply()` uses `.update(customer=record)`, which skips
  `updated_at` and `field_meta` and writes no FieldChange row (`services.py:83-86`). The history of the
  `customer_salesforce_id` value is kept from the webhook, so no history is lost, but the link change is
  invisible to history and to `updated_at`.
  - Suggested owner: Phoebe, low priority.

- **[severity: minor]** A naive `occurredAt` (no offset) is accepted by DRF's `DateTimeField` and is then read
  as UTC (`settings.py:123` `TIME_ZONE = "UTC"`). That time drives the ordering rule. The brief says
    `occurredAt` is "the time Salesforce gives the event", and the sample is `...Z`. Rejecting naive values
  would be stricter.
  - Suggested owner: Phoebe (`serializers.py:48`, a check that `tzinfo` is set).

### Scope note

- The T-007 echo-branch fix (`services.py:56-64`) and the `apply()` ordering rules belong to phase 2. They are
  covered here only where the webhook path depends on them.
- `tasks.py:197-199` (outbound ID write, the linkage gap above) belongs to phase 5.
- V-001 items 1 and 2 (phase 2 echo fix, phase 1 admin permissions) are outside phase 3 and are not re-checked here.

## Phase 2: `apply()`, the rules core — reviewed by agent-rachel

**Verified independently (read-only, no tests run):** read `docs/Round1.html` (the conflict,
history, webhook-order, duplicate, echo and 6-hour paragraphs), `src/sync/services.py` (all of
`apply()` and its helpers, 216 lines), `src/sync/models.py` (`SyncRecord.field_meta` comment at
lines 18-22, `ChangeEvent` 61-71, `FieldChange` 74-101, `Outbox` 104-146), `docs/design.md` §7-9,
`docs/build-plan.md` Phase 2 (2.1-2.4), `docs/tasks/T-003-apply-engine.md` (DEFAULTED items),
`docs/tasks/T-007-echo-sf-value-fix.md`, `src/sync/tests/test_apply.py` (test names and bodies at
lines 125-713), `src/sync/views.py:53-63` (the only caller of `apply()`), and the V-001 phase-2
section. Not executed: `pytest`, `check`, `makemigrations`, `-m brief`.

### Findings

- **[severity: important]** A Salesforce webhook that is superseded leaves `field_meta[f].sf_value`
  stale. So a later genuine Salesforce edit back to that value is dropped with no history row.
  - Brief says (`docs/Round1.html`): "Dropping that history counts as losing a change." and
    "The current value of a field is the one we can justify."
  - Code: `src/sync/services.py:66-73` (superseded branch calls `_write_history` only; `_apply_field`,
    the only writer of `sf_value` for Salesforce changes, runs only when `is_newer`);
    `src/sync/services.py:130` (`_changed_fields` compares with `sf_value`, so an equal value is
    dropped). The model comment says `sf_value` is the "last value Salesforce sent"
    (`src/sync/models.py:19-20`), which a superseded Salesforce event contradicts.
    Pinned by `src/sync/tests/test_apply.py:465-471`
    (`test_ac_2_2_4_superseded_field_leaves_field_meta_alone`).
  - Gap: sequence, reasoned from the code and not executed. Baseline city `C0` at T0 (`sf_value`
    `C0`). A portal save sets city `P` at T2: applied, `sf_value` stays `C0`, outbox row pending.
    A Salesforce webhook for city `C1` with `occurredAt` T1 (T0 < T1 < T2) arrives late: `C1 != C0`,
    so it is `superseded` and `sf_value` stays `C0`, although Salesforce did send `C1`. A genuine
    Salesforce edit back to `C0` at T4 > T2 then arrives before our `P` echo has been applied:
    `_changed_fields` sees `C0 == sf_value`, returns nothing, and no `FieldChange` row is written. The
    T-007 echo fix closes the window once the echo of `P` lands (it sets `sf_value` to `P`), so the
    window lasts until that echo, or longer if the push is stuck in the outbox during an outage.
  - Suggested owner: user or Monica decides the rule first (should a superseded Salesforce event
    still update `sf_value`? Doing so reverses the test at `test_apply.py:465`, and design §7 and
    §9 do not cover it). Then Phoebe (code), then Ross (the sequence above as a test).

- **[severity: minor]** `_notify_outbound` still swallows every exception. This is the V-001 phase-2
  item 3, still open.
  - Brief says (`docs/Round1.html`): "Pending calls to Salesforce must still be there after our own
    process restarts."
  - Code: `src/sync/services.py:91-98` (`except Exception:` at line 95, `pass` at line 96; the
    on-commit hook is at line 98). The outbox row itself is written in the same transaction (lines
    88-89), so the row is durable. A failed wake-up costs latency only, if the phase-5 sweep
    (`src/sync/tasks.py:279`, `sweep_outbound`) picks the row up. I did not verify that sweep here.
  - Gap: a failed wake-up is invisible in logs. Not a loss of data.
  - Suggested owner: Phoebe (log the exception with `logger.exception`).

- **[severity: minor]** The customer-arrival link still uses `QuerySet.update()`, which skips history
  and `updated_at`. This is the V-001 phase-3 minor (still open), listed here because the code sits
  inside `apply()`.
  - Brief says (`docs/Round1.html`): "Dropping that history counts as losing a change."
  - Code: `src/sync/services.py:81-86` (`Contact.objects.filter(...).update(customer=record)`). No
    `FieldChange` row, no `updated_at` bump. The write uses an index (`customer_salesforce_id`,
    `models.py:58`).
  - Gap: `Contact.customer` is a synced field, and its value changes here with no history row.
    It is arguably a derived link, so this is a decision as much as a fix.
  - Suggested owner: user decides whether derived links need history. If yes, Phoebe, then Ross.

- **[severity: minor]** `created_by` is never set for a portal-created record. `_lock_record` returns
  `created=False` for every portal change (`src/sync/services.py:112-113`), and `created_by` is set
  only when `created` is true (`services.py:75-76`).
  - Brief says (`docs/Round1.html`): "They need to know who changed the phone, and who changed the city."
  - Code: `src/sync/services.py:75-76`, `:112-113`. Design §4 says `created_by` is set by `apply()`.
    `test_ac_2_2_4_created_by_set_on_create_and_never_overwritten` (`test_apply.py:489`) covers only a
    Salesforce create.
  - Gap: a record created through the portal has an empty `created_by`. The history rows still carry
    the actor on every field, so the change itself is not lost.
  - Suggested owner: Phoebe (set `created_by` on a portal create, or record the creator in the portal
    create path in phase 7), then Ross.

- **[severity: minor]** The history `value` for `Contact.customer` depends on the source. A portal
  change stores the internal pk as text, and a Salesforce change stores the Salesforce id
  (`src/sync/services.py:148-151`, `:176-183`). A portal value that names no existing customer is
  still written to history as the raw pk.
  - Brief says (`docs/Round1.html`): "The earlier values, who made them, and when, stay on record."
  - Gap: the same customer reads differently in history depending on who changed it, so the history
    cannot be compared across sources.
  - Suggested owner: Phoebe (store the Salesforce id, or pk plus source, consistently), then Ross.

### Checked and held (no gap)

- **T-007 echo fix (V-001 critical item 1): confirmed fixed.** `src/sync/services.py:56-64`: for each
  field, write an `echo` `FieldChange`, then `if _is_newer(change, record, name)` set
  `field_meta[name]["sf_value"] = value`, then `record.save(update_fields=["field_meta"])`. The
  record columns, `at`, `source`, `by`, `created_by`/`updated_by` and the outbox are not touched. A
  late echo cannot overwrite a newer real value (`_is_newer` gate). Tests:
  `test_apply.py:345-362` (portal edit, echo, then Salesforce revert is `applied`; this is the V-001
  repro), `:366-378` (older echo leaves `sf_value` as `Delhi`), `:314-326` (echo writes history and
  changes only `sf_value`). The V-001 phase-2 item 2 (a test gap for the newer-equal case) is closed by
  `:345`.
- **Duplicate webhook:** `ChangeEvent.event_id` unique in the database (`src/sync/models.py:71`);
  insert in a nested savepoint before any lock, returning `duplicate` (`services.py:47-52`); test
  `test_apply.py:160`, race `ConcurrentApplyTests` (`:647-653`).
- **Out-of-order webhook:** `_is_newer` (`services.py:135-142`) compares `occurred_at` against the
  stored per-field `at`; an older event is written as `superseded` and never becomes current
  (`services.py:68-70`); test `test_apply.py:405-414`. Exact tie goes to Salesforce
  (`services.py:140-141`); test `:430-441`.
- **Two fields in one change both survive:** `_changed_fields` and `_is_newer` are per field; test
  `:417`. A whole-record webhook does not undo a newer portal value (`services.py:130`; test
  `:271-279`, which is also the brief's own city-survives scenario, marked `@brief`).
- **Sync loop:** only a portal change that applied at least one field creates an outbox row
  (`services.py:88-89`). Salesforce-sourced changes never do; test `test_apply.py:546-551`
  (`@brief`). Echoes are identified by `actor == SALESFORCE_INTEGRATION_USER` (`services.py:103-104`).
  If that assumption fails, the echo is applied as a Salesforce change and still never sent back,
  which matches design §9.
- **6-hour outage (apply's part):** the edit and its outbox row commit in one transaction
  (`services.py:45-46`, `:88-89`), so a pending call is on disk before any network call. No network
  import in `services.py` (`test_ac_2_6_services_module_imports_no_network_or_queue_code`,
  `test_apply.py:683`).
- **Numbers:** `salesforce_id` is unique and indexed (`models.py:17`); `customer_salesforce_id` is
  indexed (`models.py:58`); the outbox partial unique constraint (`models.py:139-145`) keeps one
  pending row per record. `_is_newer` and `_changed_fields` work on the locked row only, with no
  unbounded query. The outbox merge keeps `operation="create"` (`services.py:207-216`; Q18).
- **Concurrency:** `select_for_update` on the record before any read-compare-write
  (`services.py:107-117`, `:54`); design §7 says the same.
- **DEFAULTED items reviewed:** Q15 (integration user setting), Q16 (unknown record raises and rolls
  back), Q17 (echo never creates), Q18 (create kept on merge). Each matches the code and is recorded in
  design §1 per T-003. None contradicts the brief's text. Q16 rolls back the `ChangeEvent`, so a retry
  is not misreported as a duplicate, which fits the brief's duplicate rule.

### Scope note

- Portal create (build-plan 2.3.3, "a portal create → an outbox row with `operation=create`") is only
  partly covered here. `apply()` treats a portal change as an update of an existing pk
  (`services.py:112-113`). Creating the row for a portal create is phase 7 (no portal views exist yet,
  `test_ac_2_6_no_portal_views_in_this_phase`). Not a phase-2 gap, but the phase-7 create path must call
  `apply()` after inserting the row, and `docs/design.md` does not specify it.
- The echo-for-unknown-record 500 is phase 3 (reported in the Phase 3 section above). The raise itself
  (`services.py:118-119`) is the T-003 DEFAULTED Q17 behaviour and is correct inside `apply()`.
- The outbound ID write (`tasks.py:197-199`) and its missing linkage belong to phase 5 (Phase 3 section).

## Phase 5: Outbound (path B) and the outage — reviewed by agent-rachel

**Verified independently (read-only, no tests run):** read `docs/Round1.html` (line 630 and lines 647-650: the
Rate limit, Outages and Consistency paragraphs and the drain-time request), `docs/tasks/T-006-outbound-and-outage.md`,
`docs/build-plan.md` §5 (lines 392-448), `docs/design.md` §6 (Q7, Q10) and §10 (drain arithmetic, lines ~355-420),
and in full `src/sync/client.py`, `src/sync/limiter.py`, `src/sync/tasks.py` (current on-disk version, 283 lines),
`src/sync/models.py` (`Outbox`, `SyncState`), `src/sync/services.py:80-100` (`on_commit` hook),
`src/config/settings.py:154`, `src/sync/tests/test_limiter.py`, `src/sync/tests/test_outbound.py`, and the test
list in `src/sync/tests/test_client.py`. Did not run pytest, `manage.py check`, `makemigrations`, or any uv command.
Results below are by reading only.

Note: `src/sync/tasks.py` changed on disk during this review. The current version adds a self-chaining batch
(`tasks.py:170-176`) and a stop-batch-when-down check (`tasks.py:166-168`). Both are reviewed as they are now.
The T-006 ledger describes the earlier version, and its "190 passed" result predates this change, so it is stale.

### Findings

- **[severity: critical]** The limiter does not cap requests at the configured rate over any one-second window.
  A full bucket can release `rate` calls at once, then `rate` more a second later.
  - Brief says (`docs/Round1.html`): "The CRM API allows at most 100 requests per second." and "The code must
    enforce the cap it is given." The cap "counts the calls we make, including retries."
  - Code: `src/sync/limiter.py:32` sets `local capacity = rate`, and `:35` starts a fresh bucket full
    (`tokens = capacity`). So up to `rate` calls can go out back to back, then about `rate` more after one second.
    At the default `SALESFORCE_RATE_LIMIT=90` (`src/config/settings.py:154`), that is up to about 180 calls in one
    second, above the brief's 100. Design §6 and Q10 hold 10 req/s back for retries on the assumption that the cap is
    a steady per-second rate, so that headroom does not exist.
  - Gap: the bucket is sized as a one-second burst, not a steady rate with a small burst. Any second with a full
    bucket can exceed the cap. Retries count toward the cap, so 429 and 5xx retry traffic adds to the overrun.
  - Test gap: `src/sync/tests/test_limiter.py:41-64` (`test_ac_5_2_1_simulated_clock_rate_enforcement`) makes five
    acquires at t=1000.0 and five more at t=1001.0, and asserts all ten succeed. That is ten calls in the window
    [1000, 1001], asserted as correct. The build-plan done-when for 5.2 ("100 calls take ≥ 19 s (no window exceeds
    the limit)") is not what the test checks: it makes 10 calls, not 100, and never counts calls per window. A
    total-time check alone would not catch this either, because the first second still carries 10 calls.
  - Suggested owner: Phoebe (code fix: keep the refill at `rate` but cap the burst at a small number, for example 1,
    or use a sliding one-second count, so no one-second window exceeds `rate`). Ross (a test that records every
    acquire on the simulated clock and asserts the maximum count in any 1-second window is ≤ rate, and a drain test
    through `send_outbound` that checks the same).

- **[severity: important]** The auth-error path does not stop sending, and does not raise the alert the task and design require.
  - Brief says (`docs/Round1.html`): nothing explicit about 401/403. The stop-and-alert rule is ours:
    `docs/design.md` §6 (Q7): "401/403: stop all sending and page."
  - Build-plan 5.3.2 and T-006 AC-5.3.2: "401/403 → stop and alert", "halts batch, sets `SyncState(key="salesforce")`
    auth error alert."
  - Code: `src/sync/tasks.py:163-165` only logs and breaks out of the current batch. `tasks.py:235-238` stores
    `last_error` and clears the lease. Nothing writes an alert to `SyncState`. The row stays `pending` with no backoff,
    so `sweep_outbound` (`tasks.py:277-283`, every 5 s) claims it again and sends again. That is one request per 5 s
    against a 401, indefinitely, not "stop all sending."
  - Gap: the stop and the alert are not implemented, and nothing stops the 5-second re-send loop. No test covers this
    path: `test_outbound.py` has no auth case (`grep -n "auth\|401"` returns nothing).
  - Suggested owner: Phoebe (set an auth-error value on `SyncState`, have `send_outbound` return early while it is set,
    and give the row a backoff or lease). Ross (a test: a 401 stops the batch, and the next sweep sends nothing until
    the state is cleared).

- **[severity: important]** The brief-check test for restart-while-down never puts Salesforce into the `down` state.
  - Brief says (`docs/Round1.html`): "Pending calls to Salesforce must still be there after our own process restarts."
    The brief's check is "a pending call that is still there after a restart while the fake Salesforce is down."
  - Build-plan 5.5.1: "run the outbound pass in a **separate worker process**, kill it, start a new one, switch the fake
    to `up`."
  - Code: `src/sync/tests/test_outbound.py:221-285`. Two rows fail with `unreachable`, which gives
    `consecutive_failures=2`. The threshold is 5 (`tasks.py:19`), so `SyncState` stays `up`. The test then calls
    `record_salesforce_success()` (`:266`) and does not use the probe. The "restart" is `client.close()` and a new
    `SalesforceClient` in the same process (`:257-259`). The worker is never a separate process, and the test calls
    `send_outbound(...)` directly, not through Dramatiq. It also resets `next_attempt_at=now` (`:260-262`), which
    removes the backoff a real restart would still have.
  - What it does prove: the pending count in Postgres is still 2 after the down pass (`:255`), and the rows are sent
    after switching back. Durability in Postgres is real (`Outbox` model, `models.py:104-146`), so the outcome is
    right. What is missing is the down state, the probe path, and a real process restart.
  - Gap: the brief check passes without exercising "down" (sending stops, the probe runs) or a worker process restart.
    The build-plan done-when for 5.5 is not met as written.
  - Suggested owner: Ross (rewrite the test to drive five failures, let `probe_salesforce` detect recovery, and run the
    worker in a subprocess that is killed and restarted, using `serve_fake_salesforce`). Phoebe only if the subprocess
    setup needs a helper.

- **[severity: important]** The drain's request cap and the new self-chaining batch have no test.
  - Brief says (`docs/Round1.html`): "Drain the backlog as fast as the rate limit allows. Compute how long that takes,
    in minutes."
  - Code: `src/sync/tasks.py:170-176` re-enqueues `send_outbound` when a batch of `max_rows` (50) was full, and
    `tasks.py:166-168` stops the batch once the state is `down`. Both were added after the T-006 run. No test in
    `test_outbound.py` drives more rows than one batch, checks that the chain continues, or measures the request rate
    while draining. The only batch-size test is `processed == 2` with `max_rows=10` (`test_outbound.py:269-270`).
  - Gap: how fast the backlog empties, and the cap during the drain, are both unverified. The design's drain figure
    (§10: 360·r ÷ (90 − r) min, 21.2 min at r = 5/s) assumes the drain runs at R = 90 req/s, and nothing checks that.
  - Suggested owner: Ross (a test that queues more than 50 rows, drains them through the limiter on the simulated
    clock, and asserts that no one-second window exceeds the limit and the drain finishes in the expected number of
    batches). Phoebe for any fix the test shows is needed.

- **[severity: minor]** `make_all_pending_due()` clears `lease_until` on every pending row, including rows another
  worker is sending right now.
  - Code: `src/sync/tasks.py:62-65`. The update sets `lease_until=None` for all `state=pending` rows.
  - Gap: a row claimed a moment earlier can be claimed a second time, so the same call can go out twice. Upsert by
    external ID and PATCH are both idempotent for the same values, so the risk is a duplicate request inside the cap,
    not a lost or wrong update. The row's `fields` could still change between the two sends.
  - Suggested owner: Phoebe (only touch rows whose lease has expired, or none at all, since `next_attempt_at` is
    already set to now). Low priority.

- **[severity: minor]** The limiter's rate changes are read-then-write, not atomic.
  - Code: `src/sync/limiter.py:128-130` (`throttle`) and `:135-138` (`recover`) read the rate with `get_current_rate()`
    and then `SET` it. Two concurrent 429s, or a 429 during a recovery tick, can lose one halving.
  - Gap: the rate can end higher than the 429 intended. The effect is small, but the brief says "if Salesforce tells us
    to slow down, we slow down."
  - Suggested owner: Phoebe (do the halving and the +5 step inside the Lua script). Low priority.

- **[severity: minor]** `SalesforceClient._send` maps every unexpected exception to `unreachable`.
  - Code: `src/sync/client.py:76-77` and `:87-88` (`except Exception as exc: ... outcome="unreachable"`).
  - Gap: a bug in our own code (for example a serialization error) counts as an outage. It feeds the 5-failure down
    counter (`tasks.py:201-202`) and can put the whole system into `down`. The brief's outage rule is about Salesforce
    being unreachable, not our bugs. Narrow the catch to `httpx` errors.
  - Suggested owner: Phoebe. Low priority.

### Checked and found matching (no gap)
- Client URLs and headers: `upsert` and `update` use `PATCH .../sobjects/{type}/external_id/{id}` and
  `PATCH .../sobjects/{type}/{id}`, with a Bearer token (`client.py:50-58`, `:44-48`).
- Outcome mapping: 200/201/204 → ok; 429 → throttled with `Retry-After` (default 1); 400 → rejected; 401/403 →
  auth_error; 5xx and transport errors → unreachable (`client.py:90-115`). `test_client.py` has one test per outcome.
- Durability: pending work is an `Outbox` row in Postgres (`models.py:104-146`). The sweep and the `on_commit` nudge are
  only wake-ups, so a lost Dramatiq message loses no work (`services.py:93-98`, `tasks.py:277-283`).
- Claim: `SELECT ... FOR UPDATE SKIP LOCKED` with a 60 s lease, and a lease-expiry retry path (`tasks.py:84-97`).
- Backoff: `min(2**(attempts-1), 60)` (`tasks.py:204`), matching design §6 and the 1, 2, 4, 8 s … 60 s rule.
- Down detection and probe: 5 consecutive unreachable → `down` (`tasks.py:45-59`); probe every 10 s, through the limiter
  (`tasks.py:241-266`); on success it sets `up`, makes pending rows due and triggers a send.
- 429: `throttle` pauses the limiter and halves the rate; `recover` adds 5 req/s (`limiter.py:119-140`); the 30 s
  interval is on `recover_limiter_rate` (`tasks.py:269-274`).
- All sends (new, retry, probe) call `limiter.acquire` before the request (`tasks.py:148`, `:250`), so retries count
  toward the cap, as the brief requires.
- Numbers: design §10 arithmetic checks (B = 5 × 21,600 = 108,000; 108,000 ÷ 85 = 1,271 s ≈ 21.2 min).

### Scope note
- Catch-up (`nextRecordsUrl` paging, the 54-call catch-up, and its place in the drain order) is phase 6. The phase 4
  scope note asked that the client treat `nextRecordsUrl` as opaque. `client.py:60-68` (`query_url`) takes a full URL and
  does not build offsets, so that holds for now. Phase 6 must check it.
- The phase 3 section mentions `tasks.py:197-199` (outbound ID write, the linkage gap). I did not independently assess
  that gap. It belongs to phase 5 and the phase 3 reviewer's finding stands as they wrote it.
- `Outbox` indexes (`outbox_state_next_idx`, the partial unique on pending) support the claim query at 5M-row scale.
  The `make_all_pending_due` update is not paginated, but it only touches pending rows. Not a phase 5 gap at this volume.
- `SALESFORCE_RATE_LIMIT` defaults to 90 (`settings.py:154`), consistent with design Q10. The 100 cap is the brief's;
  90 leaves the headroom the design intends, but only once the burst finding above is fixed.

## Monica's routing decisions
<!-- Filled in after all five reports land. -->

All five phase reviews are in. I independently re-verified the new critical finding (phase 5's
rate limiter) and the phase 4 double-logging claim myself by reading the live code, not just the
agent reports.

### Operational note: concurrent edits during this audit

While the five reviewers were working, a process outside this session (not one of my dispatches —
`ListAgents` shows only my own, already-completed, subagents) made substantial edits to
`src/sync/tasks.py` (140 lines), `src/sync/tests/fakes.py`, `src/sync/services.py` (a small
defensive fix), and `docs/build-plan.md` (phase 6.1's subtasks ticked done, dated 2026-11-27).
This matches the user's own earlier heads-up ("other agent is also working") — most likely a
separate session building Phase 6 (catch-up). Two consequences for this audit:
- The phase 4 and phase 5 reviewers each saw a snapshot of `fakes.py`/`tasks.py` partway through
  that other session's edits, so a couple of their findings may already be partly stale. I
  re-read the current file state myself where it mattered (below) rather than trust the
  snapshot blindly.
- Phase 6 is now apparently in progress/partly done, outside this audit's requested scope
  (phases 1-5). Not reviewed here; flagging so the user knows it's moving.

### Critical (2)

1. **Rate limiter does not cap at the brief's 100 req/s (phase 5).** Re-verified myself by
   reading `src/sync/limiter.py:30-52` directly, current state. The Lua script's token bucket
   sets `capacity = rate` and starts `tokens` at `capacity` the first time a key is read
   (line 35: `tonumber(redis.call('get', tokens_key) or capacity)`). So a fresh or
   sufficiently-idle bucket allows an instantaneous burst of up to `rate` requests (90 by
   default), and after any ~1s refill window, another `rate` tokens are available — meaning
   two bursts straddling a boundary can land ~2x `rate` inside one rolling second. The brief's
   number ("at most 100 requests per second to the Salesforce record API, retries included") is
   a hard cap with no stated slack for bursts. `src/sync/tests/test_limiter.py:41-64` currently
   encodes the burst as expected behavior (asserts all 10 acquisitions succeed across two
   timestamps one second apart), so the test would not catch a regression here — it already
   doesn't catch the bug. **Escalating:** this needs a fix (e.g., cap burst well below `rate`,
   or move to a strict sliding-window count) before phase 5 can be called brief-compliant on its
   hardest number. Not reversing a "Decided" item — `design.md`'s token-bucket choice is sound,
   just under-constrained in its capacity parameter — so this doesn't need a user decision on
   approach, only a priority call on when to fix it.
   - Suggested owner: Phoebe (cap the burst / tighten capacity), Ross (a test that asserts no
     more than `rate` acquisitions succeed in any rolling 1-second window, including the
     fresh-bucket case).

2. **Django admin still bypasses `apply()` for Customer/Contact (phase 1, = V-001 item 2,
   re-confirmed, still open).** `src/sync/admin.py:20-37` still defines no permission or
   `save_model` overrides for `CustomerAdmin`/`ContactAdmin`, and `readonly_fields` leaves
   `salesforce_id`, `external_id` and `field_meta` directly editable by staff — worse than V-001
   described (not just the business fields; the sync bookkeeping fields themselves are editable).
   **Still waiting on the user's product decision**: should staff be able to edit Customer/Contact
   through the admin at all, and if so, routed through `apply()` (e.g. via `save_model`), or
   should the admin be read-only for these two, matching the other four models?

### Important (filed together as one follow-up build task once the user has weighed in above)

3. **Superseded Salesforce webhook leaves `sf_value` stale, so a later genuine Salesforce value
   equal to that stale `sf_value` is silently dropped (phase 2, new).** Same root shape as the
   T-007 echo bug, different trigger: `_changed_fields` filters a field out before the ordering
   check even runs, so no history row is written. Window is open only while a portal push for
   that field sits un-echoed in the outbox. `test_apply.py:465` currently pins today's (buggy)
   behavior, so this needs a decision before Phoebe touches it, the same way T-007 did.
4. **`changedFields` values aren't type/length-checked before `apply()` runs (phase 3, = V-001
   item 4, confirmed, now concrete)**: an over-length string reaches Postgres and fails as an
   unhandled 500, not a 400.
5. **The `superseded`-vs-`no_change` check queries `FieldChange.event_id`, which has no index
   (phase 3, = V-001 item 5, confirmed)**: a sequential scan on every `no_change` webhook response
   at 5M+ history rows.
6. **An echo for a record we don't hold yet raises `UnknownRecord`, uncaught, returning a 500
   (phase 3, new)**: touches the sync-loop rule; can occur in a narrow crash window between an
   outbound create's response and the record existing locally.
7. **`fakes.py`'s HTTP query-paging path raises `NameError`** (`quote` used, not imported) —
   **re-verified myself just now, still present in the current file**, independent of the
   concurrent edits elsewhere in the file (phase 4).
8. **Live-server parity gap**: most of `FakeSalesforce`'s failure modes and all of its paging are
   only tested via `MockTransport`, not the live-server fixture phase 5.5 depends on (phase 4).
9. **`FakeSalesforce`'s `nextRecordsUrl` is not an opaque cursor** the way real Salesforce's is,
   so a client written against the fake's `offset=` scheme could pass tests and fail against the
   real API (phase 4, restates V-001 item 11; re-check when phase 5's `client.py` is next
   audited, since that's where it'd actually bite).
10. **Auth error (401/403) doesn't stop sending or alert (phase 5)**: design §6 says "stop all
    sending and page"; currently only logs and breaks the one batch, no `SyncState` write, so
    `sweep_outbound` keeps re-sending. *Caveat: `tasks.py` changed substantially (140 lines)
    during this audit from the concurrent session noted above — re-verify this still matches
    before filing a fix.*
11. **Restart-while-down and drain-rate brief checks are not actually exercised (phase 5)**: the
    existing "restart" test never reaches the down threshold and simulates recovery directly
    rather than through the probe; no test measures the drain rate against the limiter cap. Same
    caveat as #10 — re-verify against the current `tasks.py`/`test_outbound.py` before filing.

### Minor (12) — accepted as documented, tracked for later, no action now

- Phase 1: unguarded `requeue` admin action (view-permission staff can trigger it);
  `create_integration_user` reprints the token on every re-run (user should decide print-once vs
  print-always).
- Phase 2: `_notify_outbound` swallows exceptions (= V-001 item 3, still open); customer-linkage
  `.update()` bypasses history (= V-001 item 9, still open); `created_by` never set for a
  portal-created record; `Contact.customer` history value differs by source (pk vs salesforce_id).
- Phase 3: outbound-assigned Customer IDs don't link waiting contacts (user should decide if that
  counts as "arrival"); naive `occurredAt` accepted and read as UTC.
- Phase 4: the `our_echo` scenario test is a tautology (= V-001 item 7, confirmed); upsert status
  codes don't match real Salesforce semantics (external knowledge, low priority); the brief-marked
  test only posts `sales_edit`, not `duplicate`/`stale_after`, in that file (covered elsewhere).
- Phase 5: `make_all_pending_due` clears the lease on all pending rows (idempotent, low risk);
  limiter `throttle`/`recover` are read-then-write, not atomic; `SalesforceClient` maps every
  unexpected exception to "unreachable", masking real bugs as outage.

### Corrected, not confirmed

- Phase 4's "MockTransport logs every non-down call twice" claim: **re-read `fakes.py:181-192`
  myself just now and it does not reproduce.** The current `handler` only appends to `fake.calls`
  once, in the `down` branch; the non-down branch relies solely on `_handle_request`'s single
  internal append. Each path logs exactly once. Most likely the reviewer caught an intermediate
  state of the file mid-edit by the concurrent session noted above (that session appears to have
  been actively fixing V-001 item 6's missing down-mode logging while the phase 4 reviewer was
  reading the file). Not filing this one — note it in case it resurfaces.

**Status of this audit:** report delivered to the user. No fixes dispatched yet this round — the
user asked for the report first. Item 1 (rate limiter) and item 2 (admin bypass) need the user's
call before Phoebe starts; items 3-11 can be filed as one build task once items 1/2 are settled,
the same pattern as V-001.
