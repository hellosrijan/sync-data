# V-001: Brief-alignment audit, phases 1-4

**Status:** awaiting user decisions (items 1, 2)
**Owner:** agent-monica
**Requested:** 2026-11-27 by the user: "Most of the task has been done by the other agent. I
want you to update the skill of the agent to verify the completed task. make sure that the code
written is aligned with the Round1.html file and in case you find any gap assiged this to agent
monica. Verify till phase 4. Each agent will pick one phase."

**Purpose:** phases 1-4 are all marked done in `docs/build-plan.md` (ledgers T-001, T-002,
T-003, T-004, T-005). This is an independent, read-only audit of that claim against
`docs/Round1.html` itself, not against our own `design.md`/`build-plan.md` paraphrase of it.
Four `agent-rachel` instances, one per phase, dispatched concurrently (each phase is
independent, already-built work — no Phoebe→Ross-style ordering needed). See
`.claude/skills/brief-alignment-review/SKILL.md` for their procedure, and
`.claude/rules/agent-team.md` ("Brief-alignment audits") for how this differs from a normal
build task.

**Phase scope (from `docs/build-plan.md`):**
- Phase 1 — Foundations: models, admin, auth, `api/v1/` URL layout (ledgers T-001, T-002)
- Phase 2 — `apply()`: the rules core (ledger T-003)
- Phase 3 — Inbound webhook: `WebhookView`, token auth, customer linkage (ledger T-004)
- Phase 4 — Test scenarios: `FakeSalesforce`, `MockTransport`, scenario builders (ledger T-005)

Phase 5 (`T-006`, outbound/outage) exists and is marked done but is **out of scope** for this
audit — the user asked for phases 1-4 only.

## Log

### 2026-11-27 agent-monica: audit set up, dispatching four reviewers
- Re-established ground truth after a context resume: 190 tests pass, `check`/migrations clean,
  ledgers T-001 through T-006 all say `Status: done`, build-plan Status table shows phases 0-5
  done, 6-9 not started.
- Wrote `.claude/skills/brief-alignment-review/SKILL.md` and updated `.claude/agents/agent-rachel.md`
  (she now has two modes: unblocking, and this audit) and `.claude/rules/agent-team.md` (new
  "Brief-alignment audits" section).
- Dispatching agent-rachel × 4, one per phase (1, 2, 3, 4), concurrently.

## Findings
<!-- One section per phase, appended by that phase's agent-rachel. Never edit another phase's section. -->

## Phase 1: Foundations — reviewed by agent-rachel

**Verified independently:** `uv run pytest -q` -> "190 passed in 16.47s" (a first run right after
`docker compose up` showed "29 passed, 161 errors", not reproduced on the re-run; cause not
established, possibly the other concurrent reviewers sharing the test database), `check` ("System
check identified no issues (0 silenced)"), `makemigrations --check --dry-run` ("No changes
detected"), `-m brief` ("10 passed, 180 deselected").

### Findings

- **[severity: critical]** Django admin lets staff add, change and delete Customer and Contact
  directly, which saves without `apply()`: no `FieldChange` history row and no `Outbox` row, so
  Salesforce never gets the edit.
  - Brief says (`docs/Round1.html`): "The earlier values, who made them, and when, stay on record.
    Dropping that history counts as losing a change." and "Salesforce should show the new city."
  - Code: `src/sync/admin.py:21` (`CustomerAdmin`) and `src/sync/admin.py:30` (`ContactAdmin`)
    define no `has_add/change/delete_permission`, so Django defaults apply. Run as a superuser
    (`RequestFactory` check, no DB writes): `Customer add True change True delete True`,
    `Contact add True change True delete True`. `src/sync/tests/test_admin.py:114-122` only checks
    the three read-only models.
  - Gap: an in-scope field (for example `Contact.city`) can be changed in admin with no history and
    no outbound call. That is the "losing a change" the brief rules out. It also breaks the team's
    own rule in `.claude/rules/drf.md` ("Never write them with ... `Model.save()`").
  - Suggested owner: Phoebe (code fix: make both admins read-only, or route save through `apply()`
    in `save_model`), with Ross adding a test that a change POST does not write a row. Whether
    staff may edit through admin at all is a product decision: Monica to decide, or ask the user.

- **[severity: important]** Admin search on the 5M-row tables cannot use its indexes.
  - Brief says (`docs/Round1.html`): "5 million records in production, customers and contacts
    counted together."
  - Code: `src/sync/admin.py:24` (`CustomerAdmin.search_fields = ("external_id", "salesforce_id")`)
    and `src/sync/admin.py:33` (`ContactAdmin.search_fields` with `email`, `salesforce_id`,
    `customer_salesforce_id`, `external_id`). Django's default lookup is `icontains`. Compiled SQL
    (`Contact.objects.filter(email__icontains=...)`): `WHERE UPPER("sync_contact"."email"::text)
    LIKE UPPER(%...%)`, the same shape for `salesforce_id`. A leading-wildcard `UPPER(...) LIKE`
    cannot use the b-tree indexes on `email`, `salesforce_id`, `customer_salesforce_id` or
    `external_id`, so every admin search scans the whole table.
  - Gap: the search column is indexed but the lookup is not, so each search is a full scan at
    5M rows. The existing test `test_ac_1_4_1_search_fields_are_indexed_columns_only` checks the
    column names only, not the lookup type.
  - Suggested owner: Phoebe (exact or prefix lookups, for example `"=salesforce_id"`, `"=email"`,
    `"=external_id"`), with Ross extending that test to check the lookup prefix.

- **[severity: minor]** `FieldChange` is append-only only by convention. `FieldChangeAdmin` blocks
  edits, but the database does not, so any ORM `.update()` or `.delete()` on `sync_fieldchange`
  would succeed. A grep of `src/sync` and `src/utils` (excluding tests) finds no such call today.
  - Brief says (`docs/Round1.html`): "The fact of an earlier edit stays on record after a later
    edit."
  - Code: `src/sync/models.py:74-` (`FieldChange`, no constraint or trigger blocking UPDATE or
    DELETE).
  - Gap: the history rule depends on every future caller behaving. Acceptable for this exercise;
    a DB-level guard would make it hold by construction.
  - Suggested owner: user decision (accept as a documented assumption, or add a DB rule). Ross
    could add the test either way.

### Scope note

- `src/sync/services.py:81`: `Contact.objects.filter(customer_salesforce_id=..., customer__isnull=True).update(customer=record)`
  runs inside `apply()`. It changes the in-scope "customer they belong to" field with no
  `FieldChange` row and no `Outbox` row. It belongs to phase 2 (`apply()`) and phase 3 (build-plan
  3.1.4, customer linkage). Monica should route it to those reviewers, not this one.
- Checked and clean for phase 1: `src/sync/models.py:35` (`Customer`) has only `name`, `status`,
  `owner` as business fields; `src/sync/models.py:45` (`Contact`) has `full_name`, `phone`,
  `email`, `city`, `owner`, `customer` (+ `customer_salesforce_id`, a link column). Serializers
  expose exactly those fields (`src/sync/serializers.py`). `src/config/urls.py:23` is the only
  `api/v1/` definition. `src/config/settings.py:142-146` sets `TokenAuthentication` and
  `IsAuthenticated`, and `USE_TZ = True` at line 127. `src/utils/views.py:15` is the only `AllowAny`
  (health). Uniqueness of `event_id` and one pending outbox row per record is in the database
  (`src/sync/models.py:71`, `:143`), as the brief's duplicate rule needs.

## Phase 3: Inbound webhook - reviewed by agent-rachel

**Verified independently:** `uv run pytest -q` → **190 passed** (run with `POSTGRES_DB=sync_data_audit_phase3`, see Scope note), `check` (no issues), `makemigrations --check --dry-run` (No changes detected), `-m brief` → **10 passed**.

### Findings

- **[severity: important]** `changedFields` values are not checked. A nested object or list is accepted, applied, and stored as text.
  - Brief says (`docs/Round1.html`): "Our endpoint has to accept it, check it, and apply it before anything is stored."
  - Code: `src/sync/serializers.py:50` (`changedFields = serializers.DictField(required=True)`; only the top level is checked, `:52-55`). `src/sync/views.py:44-47` copies the raw values through. Probe (scratch test outside the repo): `changedFields: {"phone": {"x": 1}}` returned `200 applied` with `appliedFields: ["phone"]` and a `ChangeEvent` kept; `changedFields: {"city": ["a", "b"]}` returned `200 applied`.
  - Gap: a malformed value that the brief's "check" step should reject with 400 reaches `apply()` and is written to a portal column. No test covers a non-scalar value (`test_webhook.py` tests only top-level dict-ness, AC-3.1.1).
  - Suggested owner: Phoebe (per-field scalar/null check in the serializer, 400 on failure), then Ross (test for nested and list values, asserting nothing is stored).

- **[severity: important]** The `superseded` check adds an unindexed query to every `no_change` webhook.
  - Brief says (`docs/Round1.html`): "The earlier values, who made them, and when, stay on record." (the table this query scans is that history, which grows with every edit; the brief sets 5M records as the scale.)
  - Code: `src/sync/views.py:68` (`FieldChange.objects.filter(event_id=event_id, status=...).exists()`). `src/sync/models.py:95` (`event_id` has no `db_index`); the only index on `FieldChange` is `object_type, record_id, occurred_at` (`models.py:98-100`). So each `no_change` response is a sequential scan of the history table.
  - Gap: an added query on a hot path with no index, at a row count the brief sets at millions. It is not one of the four checked rules, so not critical.
  - Suggested owner: Phoebe. Simplest fix: have `apply()` return a `superseded` flag in `Result` (no query). Alternative: add an index on `FieldChange.event_id`.

- **[severity: minor]** Customer-arrival linking writes contacts with `QuerySet.update()`, which skips history and audit fields. This is the same mechanism the phase 1 reviewer flagged as "customer they belong to" with no `FieldChange` row; it is one finding, routed to Phoebe here.
  - Brief says (`docs/Round1.html`): "Dropping that history counts as losing a change."
  - Code: `src/sync/services.py:78-81` (`Contact.objects.filter(...).update(customer=record)`). No `FieldChange` row is written, and `updated_at` (`auto_now`) and `updated_by` are not touched.
  - Gap: the contact's customer link changes with no history row and no `updated_at` bump. The link is a derived result, not a value Salesforce sent, so this is arguably not a lost change. Worth a decision, not a blocker.
  - Suggested owner: Phoebe if Monica wants a history row per linked contact (Ross then adds a test). Otherwise the user decides whether derived links need history.

- **[severity: minor]** Doc drift on replay after the 7-day purge.
  - Brief says (`docs/Round1.html`): "Salesforce may deliver the same webhook twice."
  - Design says: `docs/design.md:314-317` (§8: a repeat after the window "adds a second history row") and `docs/design.md:60` (Q11: after the window it is "superseded").
  - Code and probe: a replay of an identical payload after its `ChangeEvent` was deleted returned `result="no_change"` with no `FieldChange` row, because the field's `sf_value` already matched. A replay with a different value gives `superseded` plus a history row. The design text describes only one of the two cases.
  - Suggested owner: Chandler (doc wording only).

### Scope note

- The customer-arrival link (`services.py:78-81`) also touches phase 2 (`apply()`), as the phase 1 note says. It is listed here because it is AC-3.1.4.
- Nothing else here belongs to another phase. The 7-day retention window is a design assumption (`docs/design.md:314`). The brief names no window, so it stays a documented assumption for the user to confirm.
- Checked, no gap: `purge_expired_change_events` deletes by `received_at` (indexed on `ChangeEvent`), so it does not cascade into `FieldChange` (`event_id` is a plain column, no FK). The webhook is not throttled (no `DEFAULT_THROTTLE_CLASSES` in `src/config/settings.py:142-146`, none on the view), matching the brief's "Webhooks arriving at our endpoint sit outside this cap." 401, 403 and 400 handling, and the no-write-on-failure path, are tested (`test_webhook.py`, AC-3.1.3).
- Process note for Monica: concurrent `pytest` runs from the four reviewers share one test database name (`test_sync_data`). My first run errored with `database "test_sync_data" already exists` and `is being accessed by other users`. I reran with `POSTGRES_DB=sync_data_audit_phase3`, which changes only the test DB name for that process. No repo file was changed.

## Phase 4: Test scenarios (a fake Salesforce inside the tests) — reviewed by agent-rachel

**Verified independently:** `uv run pytest -q` → 190 passed in 12.84s; `check` (0 issues);
`makemigrations --check --dry-run` (No changes detected); `uv run pytest -q -m brief` → 10 passed
when run alone (1.34s). Run while other pytest processes were active, the same command gave 10 errors
(`database "test_sync_data" already exists` / `is being accessed by other users`), so brief runs must be serial (see Scope note).

Brief text checked: `docs/Round1.html`: "If you write a program, call a fake CRM you write yourself."
Satisfied: no new app (`INSTALLED_APPS`, `src/config/settings.py:39-52`, has none), and
`docker-compose.yml` has only `db`, `redis`, `worker` (lines 4, 17, 34). The brief's four checks are
all possible to write with the fake: duplicate (`src/sync/tests/scenarios.py:61-63`), older after newer
(`stale_after`, `scenarios.py:66-75`), no echo to Salesforce (`our_echo`, `scenarios.py:44-57`),
restart while down (`mode = "down"`, `fakes.py:104`; `serve_fake_salesforce`, `fakes.py:218`).
Full wiring is phase 8, not this phase.

### Findings

- **[severity: important]** The MockTransport "down" path does not log calls, so attempts made during
  an outage (which count toward the cap) are invisible to `fake.calls` on the path the unit tests use.
  - Brief says (`docs/Round1.html`): "The cap counts the calls we make, including retries."
  - Code: `src/sync/tests/fakes.py:179-180` (`transport_for` raises `httpx.ConnectError("fake: down")`
    before `_handle_request` runs). `fakes.py:102` (`fake.calls.append`) runs only inside
    `_handle_request`. Reproduced with a scratch probe: three down-mode PATCHes through `transport_for`
    gave `calls logged = 0`; three through `serve_fake_salesforce` gave `calls logged = 3`, status 503.
  - Gap: AC-4.2.2 says the log records every incoming request. On the MockTransport path a count taken
    during an outage reads 0, while the live path counts the retries. Phase 5's rate and outage tests
    would pass on one path and not the other.
  - Suggested owner: Phoebe (code fix: append to `fake.calls` in `transport_for` before raising), then
    Ross (test: down-mode call count is equal on both paths).

- **[severity: important]** The `our_echo` assertion is always true, so the sync-loop echo helper is
  not actually tested.
  - Brief says (`docs/Round1.html`): "An update we send to Salesforce must not bounce back and forth forever."
  - Code: `src/sync/tests/test_fakes.py:262`: `assert "settings" in echo_payload["changedBy"] or echo_payload["changedBy"]`
    passes for any non-empty string. The builder is correct (`scenarios.py:50,56` use
    `settings.SALESFORCE_INTEGRATION_USER`, defined at `src/config/settings.py:153`).
  - Gap: a regression in `our_echo` (wrong `changedBy`) fails no test. The sync-loop rule depends on
    this helper's `changedBy`.
  - Suggested owner: Ross (missing test: `assert echo_payload["changedBy"] == settings.SALESFORCE_INTEGRATION_USER`).

- **[severity: minor]** Live-server failure modes and the call-log timestamp are weakly tested.
  - Brief says: n/a. Build-plan 4.1.3 and 4.2.1 "Done when" say "the same test passes through the
    transport and through the local server" and "each mode produces the documented response".
  - Code: `src/sync/tests/test_fakes.py:188-223` runs all six modes only through `mock_client`.
    `test_ac_4_1_3_live_http_server_matches_transport` (`test_fakes.py:169`) covers only a create on the
    live server. The timestamp check is `isinstance(ts1, timezone.datetime)` (`test_fakes.py:235`), which
    does not check the value.
  - Gap: no test shows the live server returns 503 in `down`, or the 429/401 headers. The "same test
    through both" done-when is met by two separate tests.
  - Suggested owner: Ross (parametrised live-server check for down/throttle/auth; assert timestamps are
    non-decreasing and inside the test window).

- **[severity: minor]** The fake's `nextRecordsUrl` drops the SOQL `q` and the `SystemModstamp` filter,
  so following it silently widens the query to the 365-day default window.
  - Brief says: n/a directly. The catch-up requirement ("Drain the backlog as fast as the rate limit
    allows") needs a correct "changed since T" query.
  - Code: `src/sync/tests/fakes.py:170` (`nextRecordsUrl = "/services/data/v60.0/query/?offset=..."`);
    `fakes.py:150-152` defaults `since` to now minus 365 days when `q` has no filter.
  - Gap: a client that follows the next URL gets the wrong record set. No current test reaches a second
    page through the transport. Real Salesforce uses a query locator. This is a fidelity gap for phase 6's
    catch-up test.
  - Suggested owner: Phoebe (carry `q` in the next URL, or use a locator-style token), then Ross (a test
    that pages through `nextRecordsUrl` and checks the window).

### Scope note

- Phases 5 and 6 (not this audit): `src/sync/client.py`, `src/sync/limiter.py` and `test_outbound.py` are
  untracked in git. The brief-marker errors came from concurrent pytest runs sharing the test database
  `test_sync_data`, not from phase 4 code. Monica should serialize pytest runs, or give each run its own
  test DB name (the phase 3 reviewer used `POSTGRES_DB` to do this).
- Ledger staleness (not a gap): `docs/tasks/T-005-fake-salesforce-scenarios.md` pastes "171 passed" and
  "9 passed"; the current counts are 190 and 10 because later phases added tests. The ledger is history,
  so leave it as is.

## Phase 2: apply() - reviewed by agent-rachel

**Verified independently:** `uv run pytest -q` → **190 passed in 8.59s**, `check` ("System check identified no issues (0 silenced)"), `makemigrations --check --dry-run` ("No changes detected"), `-m brief` → **10 passed, 180 deselected**. Concurrency, three repeats of `uv run pytest -q src/sync/tests/test_apply.py -k Concurrent`: "4 passed" ×3 (in 1.5 s each). Earlier, a run that overlapped other reviewers' pytest runs on the shared test DB failed (3 failed / 4 errors); not reproduced on the three clean runs. The live result is 4 passed.

### Findings

- **[severity: critical]** A Salesforce change that sets a field back to the value Salesforce last sent is dropped, so the portal stays on a stale value and the history misses the change. This follows the echo rule (design §9), not only the per-field check.
  - Brief says (`docs/Round1.html`): "A create or an update on either side is visible on the other side within 30 seconds." and "Edits to the same field need one current value and a kept history." and "An update we send to Salesforce must not bounce back and forth forever."
  - Code: `src/sync/services.py:56-59` (echo branch writes history only, returns before any `field_meta` change); `src/sync/services.py:125` (`meta["sf_value"] != value` → no change); `src/sync/services.py:117-127` (`_changed_fields`). Design: `docs/design.md:329-333` (echo: "No record column, `field_meta` or outbox row changes").
  - Reproduced with a rolled-back probe (`src/sync/` not edited; script `scratchpad/echo_revert.py`, run via `uv run python src/manage.py shell`, final `raise` rolls back). Sequence: SF `city=Delhi` (sales) → portal `city=Noida` (finance, applied and pushed) → SF echo `city=Noida` from the integration user → SF `city=Delhi` again from sales at a later time. Output: `outcomes applied echo no_change | portal city: Noida | sf_value: Delhi`. Salesforce holds Delhi, the portal shows Noida, and the sales edit at the latest time is recorded as nothing (no `FieldChange` row, status `no_change`).
  - Gap: after an echo, `sf_value` still holds the pre-edit value, so a later real Salesforce edit back to that value looks unchanged. The two copies diverge and the edit is lost. The brief's own scenario (sales change later, portal shows it) is only safe when the value differs from the old `sf_value`. Not covered by any test: `test_ac_2_2_2_integration_user_echo_writes_history_only` checks only that nothing changes.
  - Fix options (for Monica to choose, not a quick patch): (a) on echo, set `field_meta[f].sf_value` to the echoed value (Salesforce now holds it) while still not changing the record column or history status; this reverses the "field_meta ... change" clause of the Decided echo rule, so it needs the user's sign-off per `.claude/rules/agent-team.md` rule 2; (b) keep the rule and make the per-field check compare to the record column for Salesforce-sourced changes when the last writer was an echo. Either way, add a test for the echo-then-revert sequence above.
  - Suggested owner: user decision (the echo rule is Decided; option (a) changes it), then Phoebe (code), then Ross (the sequence as a test in `test_apply.py`).

- **[severity: minor]** No test covers "a Salesforce change whose value equals `sf_value` but which is newer than the portal edit". That is the exact case in the critical finding above. `test_ac_2_2_1_whole_record_equal_to_sf_value_is_no_change` (`src/sync/tests/test_apply.py:273`) only covers a same-time repeat.
  - Brief says (`docs/Round1.html`): "The current value of a field is the one we can justify."
  - Code: `src/sync/tests/test_apply.py:273-279`.
  - Gap: the test suite is green while the critical case fails, so the rules tests give false confidence.
  - Suggested owner: Ross (once the critical finding is decided).

- **[severity: minor]** `_notify_outbound` in `src/sync/services.py:86-91` swallows every exception with `except Exception: pass`. A failed wake-up of the worker is silent. Outbox rows are durable, so the cost is latency, not loss, and the 30 s target depends on the worker's poll interval. Suggested owner: Phoebe (log the exception) when phase 5 wiring is reviewed.

### Checked and held (no gap)

- **Brief scenario, the city survives a later phone webhook:** `test_ac_2_2_3_brief_scenario_end_to_end` (`test_apply.py:338`, marked `@brief`) and `test_ac_2_2_1_whole_record_webhook_does_not_undo_newer_portal_city` (`:262`). Both pass and assert city "New City" survives and phone "222" is applied. The outbox row for city is created once with `fields=["city"]`.
- **Duplicate webhook:** `ChangeEvent.event_id` unique in the database (`src/sync/models.py:71`), insert in a savepoint before the lock (`services.py:47-52`). Tests at `test_apply.py:151` and the same-`event_id` race at `ConcurrentApplyTests` (`:600`).
- **Out-of-order:** `_is_newer` (`services.py:130-137`) compares `occurred_at` with the stored `at`; the older event is kept as `superseded` (`test_apply.py:352`). The exact tie goes to Salesforce (`:136`, test `:377`).
- **Two fields at once both survive:** `test_ac_2_2_3_two_fields_in_one_change_both_applied` (`:364`) and the whole-record race (`:597`, `check_whole_record_race_loses_no_field`).
- **Outbox:** one pending row per record, merged (`services.py:202-211`, DB constraint `models.py:139-145`). Salesforce changes never create an outbox row (`services.py:83`, `test_apply.py:494`). The row and the edit commit together (same transaction, `services.py:45-46`).
- **Numbers:** the apply path does no network call (`test_ac_2_6_apply_makes_no_network_call`). Lookups by `record_id` / `salesforce_id` and `customer_salesforce_id` use indexed columns. The `Contact` update at `services.py:78-81` filters on `customer_salesforce_id` (indexed, `models.py:58`).

### Scope note

- The `FieldChange` lookup used by phase 3's `no_change` check (`src/sync/views.py:68`) was flagged by the phase 3 reviewer; it is not this phase's code.
- `apply()` writes `Contact.customer` through `Contact.objects.filter(...).update(...)` (`services.py:78-81`). That is phase 2 code, but the linkage rule is phase 3's (build-plan 3.1.4). Already noted by the phase 1 reviewer; no separate finding here.
- Phase 5 (`test_outbound.py`, `client.py`) was not reviewed; its ledger is T-006.

## Monica's routing decisions
<!-- Filled in after all four reports land: each finding -> accepted as-is / filed as a new build task / escalated to the user, with the reasoning. -->

All four phase reviews are in. I independently re-verified the two **critical** findings and
the two most load-bearing **important** findings myself (not just read the agent-memory
reports) before deciding routing - commands below, or see each phase's own section above for the
reviewers' evidence.

### Critical (both reversed independently; both need the user before Phoebe starts)

**Item 1 resolved 2026-11-26 — see `docs/tasks/T-007-echo-sf-value-fix.md`.** The user confirmed
the fix (echo updates `field_meta[f].sf_value` to the echoed value, gated by `_is_newer` so a late
echo can't clobber a newer real value). Shipped in `src/sync/services.py:56-64`, tested
(`test_ac3_t007_portal_edit_echo_then_salesforce_revert_is_applied`,
`test_ac4_t007_older_echo_does_not_overwrite_newer_salesforce_value`, and the updated
`test_ac_2_2_2_integration_user_echo_writes_history_only`), with a verified fail-before-fix
(reverted locally, re-ran, `3 failed` exactly as predicted; restored, `192 passed`). Docs
(`docs/design.md` §9, `.claude/skills/sync-engine/SKILL.md`, `docs/build-plan.md:277`,
`wiki/Architecture.md:200,252-253`) updated to match. Item 2 (admin bypass) is still open.

1. **Echo never updates `field_meta[f].sf_value` (phase 2).** Reproduced myself with a fresh
   repro script (`src/manage.py shell`, rolled back, `settings.SALESFORCE_INTEGRATION_USER` as
   the echo actor): portal pushes city=Noida -> Salesforce echoes Noida back (correctly
   suppressed as `echo`, no re-apply) -> sales later reverts Salesforce to city=Delhi for real ->
   `apply()` returns `no_change` and drops it, because `sf_value` was still "Delhi" from before
   the portal push and never got updated to "Noida" when the echo confirmed Salesforce had
   committed it. The portal is left stuck on "Noida" forever while Salesforce truly holds
   "Delhi" - no 30s sync, no history row, a silently lost real edit.
   - **This touches the "Decided" echo rule** in `docs/design.md` (echo = confirmation only,
     apply nothing) - not its intent (never re-apply our own change, never create a loop), but
     a gap in it (never fixing up `sf_value`, which is what every *later* comparison depends
     on). Per `agent-team.md`'s stop-condition 2, I'm not having Phoebe touch this without the
     user's sign-off.
   - **Escalating to the user:** does an echo update `field_meta[f].sf_value` to the echoed
     value (closes the gap, doesn't touch the record column, history status, or `updated_by` -
     those stay echo-only as already decided), or is there a different mechanism preferred?

2. **Django admin bypasses `apply()` for Customer/Contact (phase 1).** Confirmed directly
   (`src/sync/admin.py:19-36`): `CustomerAdmin`/`ContactAdmin` define no permission overrides,
   unlike `ReadOnlyAdmin`, so Django's defaults let staff add/change/delete both models with a
   plain `Model.save()` - no `FieldChange` row, no `Outbox` row, Salesforce never told.
   - This isn't reversing a decision - `drf.md` already says "never `Model.save()`" on synced
     records, and the admin was simply never brought in line with that existing rule. I'd
     normally just route this to Phoebe. **Escalating anyway** because the *product* question
     underneath it is the user's to answer, not mine: should staff be able to edit
     Customer/Contact through the admin at all (and if so, routed through `apply()`, e.g. via
     `save_model`), or should the admin be read-only for these two, matching the other four
     models?

### Important (filed as a follow-up build task, no user decision needed - none reverse a
Decided item or the brief's own numbers)

3. Admin search (`CustomerAdmin`/`ContactAdmin`) uses `icontains`, which can't use the b-tree
   indexes on `external_id`/`salesforce_id`/`email`/`customer_salesforce_id` at 5M rows (phase 1).
4. `changedFields` values aren't type-checked (a nested object/list is accepted and stored as
   text) before `apply()` runs (phase 3).
5. The `superseded`-vs-`no_change` check in `WebhookView` queries `FieldChange` by `event_id`,
   which has no index - a sequential scan on every `no_change` response at 5M+ history rows
   (phase 3).
6. `FakeSalesforce`'s down-mode call logging is inconsistent between the `MockTransport` and
   live-server paths - calls made while "down" aren't logged via `MockTransport`, so a test
   relying on the call log to count requests made during an outage would undercount (phase 4).
7. The `our_echo` scenario-builder test asserts a tautology (`"settings" in x or x`), so the
   sync-loop echo path's own scenario builder is effectively untested (phase 4).

### Minor (accepted as documented, tracked for later - no action now)

8. `FieldChange` has no DB-level guard against `UPDATE`/`DELETE` (append-only by convention
   only) - accepted as a known limit (phase 1).
9. The customer-linkage `.update()` in `services.py:78-81` bypasses history for the FK link
   itself (not a user-visible field) - flagged by both phase 1 and phase 3 reviewers as the same
   finding; tracked with item 4/5's follow-up task, not urgent on its own (phase 1/3).
10. Doc drift in `design.md` §8/Q11 about what a 7-day-stale replay produces (phase 3) - a
    Chandler fix, no code change.
11. `FakeSalesforce`'s `nextRecordsUrl` pagination link drops the original query filter (phase 4)
    - a fidelity gap that matters once phase 6 (catch-up) paginates through it; filed for then.
12. No tests yet for the live-server's own failure modes, separately from `MockTransport`'s
    (phase 4) - filed with item 6.

### Process note (not a product finding)

Running `pytest` from four concurrent reviewers against one shared Postgres test database
caused transient collisions (two reports saw spurious errors that didn't reproduce on a clean
re-run; one reviewer isolated itself with a separate `POSTGRES_DB`). Worth a line in
`sync-testing` skill for the next time multiple reviewers or build steps run concurrently:
either serialize `pytest` invocations across concurrent agents, or give each a distinct test DB
name. Not fixing now - noting it so it isn't lost.

**Verified myself before writing the above:** `uv run pytest -q` → 190 passed; `manage.py check`
clean; `makemigrations --check --dry-run` clean; the echo/`sf_value` repro above, run twice (once
with a wrong actor string that correctly did *not* reproduce, once with the real
`SALESFORCE_INTEGRATION_USER` value that did); `src/sync/admin.py:19-36` read directly;
`src/sync/serializers.py:50-55`, `src/sync/views.py:60-68`, `src/sync/models.py` (`FieldChange`
has no index on `event_id`) read directly; `src/sync/tests/fakes.py:95-105,170-185` and
`src/sync/tests/test_fakes.py:258-263` read directly.

**Status of this task:** item 1 resolved 2026-11-26 (`T-007`). Waiting on the user for item 2
only. Items 3-7 will be filed as a new build task (next free `T-NNN`) once the user has weighed
in on item 2, so Joey can route all of it - including any fix the user's decision implies - in
one pass instead of two.
