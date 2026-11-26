# T-001: Phase 1 foundations (settings and auth, models, admin, URL layout)

**Status:** done
**Owner:** agent-monica
**Requested:** 2026-11-27 by the user: "Let's start building the phase 1. With the phase 1 i assume you'll setup the models. I also want you to make sure each have urls.py, serializers.py and config/urls.py is the main url. all the api path will be like api/v1/customer/. you can define api/v1/ in the config/urls.py"

**Scope:** `docs/build-plan.md` tasks **1.2, 1.3, 1.4, 1.6** (and 1.5.1 once the models exist).
Out of scope: skills 1.1.x (main session), views and endpoints (phases 3 and 7), `apply()`
(phase 2). Don't build ahead.

**Sources:** `docs/build-plan.md` (phase 1, "URL conventions", "Defaults"), `docs/design.md` §4
(column layout) and §6–§9, `docs/openapi.yml` (field names, lengths), `.claude/rules/drf.md`.

## Acceptance criteria
<!-- Joey drafts; Monica approves. Each one testable. Cite the brief or design section. -->

Source key: BP = `docs/build-plan.md`, D = `docs/design.md`, API = `docs/openapi.yml`, R = `.claude/rules/drf.md`.
Commands below are run from the repo root with compose services up. Never print `.env` values.

**1.2 Settings and authentication** (Slice A)

- **AC-1.2.1** `rest_framework.authtoken` is in `INSTALLED_APPS` (`src/config/settings.py`) and
  its migration applies: `Token` rows can be created, and `manage.py migrate` on an empty DB
  succeeds. (BP 1.2.1)
- **AC-1.2.2** `REST_FRAMEWORK` in `src/config/settings.py` sets `DEFAULT_AUTHENTICATION_CLASSES`
  to `TokenAuthentication` only and `DEFAULT_PERMISSION_CLASSES` to `IsAuthenticated`. Test: a
  request to a protected URL with no token gets 401 (not 403), and with a bad token gets 401.
  `HealthView` (`src/utils/views.py`) declares `permission_classes = [AllowAny]` explicitly (and
  needs no auth, so `GET /api/v1/health/` is 200 without a token). (BP 1.2.2, R "Authentication
  and permissions", D Q14) `DEFAULTED` Q14: DRF token per portal user.
  Note for Ross: no protected view exists yet, so the 401 test may use a throwaway view or
  `override_settings`/`APIRequestFactory`; do not add a real endpoint for it (don't build ahead).
- **AC-1.2.3** Integration user: a group named exactly `salesforce-integration`; permission class
  `IsSalesforceIntegration` (in `src/sync/permissions.py`) that allows only an authenticated user
  in that group (a portal user without the group is denied, anonymous is denied); management
  command `src/sync/management/commands/create_integration_user.py` (with `__init__.py` files)
  that creates the group and user if missing, creates the token, and prints it once. Re-running
  must not crash and must not create a second user. Tests: permission class allow/deny for the
  three cases; command run via `call_command` creates exactly one user in the group with one
  token and the printed token equals `Token.objects.get(...).key`. (BP 1.2.3, D Q11, R)
- **AC-1.2.4** `httpx` is a dependency in `pyproject.toml` and `uv.lock` (added with `uv add`,
  importable). `src/config/settings.py` reads `SALESFORCE_BASE_URL`, `SALESFORCE_TOKEN` and
  `SALESFORCE_RATE_LIMIT` (int, default 90) through django-environ. `.env` and `.env.example`
  contain the same set of keys: test or command compares key names only (never values), e.g.
  parse both files for `KEY=` names and assert set equality. (BP 1.2.4, D Q10)

**1.3 Models and migrations** (Slice A). All in `src/sync/models.py`, migrations in
`src/sync/migrations/`, column layout per D §4 and the lengths in API.

- **AC-1.3.1** `Customer`: `external_id` UUID (default uuid4, unique), `salesforce_id`
  varchar(18) unique and nullable, `name` (255), `status` (40), `owner` (255), `field_meta`
  JSONField (default dict), `created_at`, `updated_at`. `Contact`: same sync columns plus
  `full_name` (255), `phone` (40), `email` (254, indexed), `city` (120), `owner` (255),
  `customer` FK to `Customer`, `null=True`, `on_delete=PROTECT`, indexed, and
  `customer_salesforce_id` varchar(18), indexed (blank default). Test: deleting a customer that
  has a contact raises `ProtectedError`; two customers with `salesforce_id=None` coexist, two
  with the same non-null `salesforce_id` raise `IntegrityError`; a duplicate `external_id`
  raises `IntegrityError` for both models. (BP 1.3.1, D §4)
  `DEFAULTED`: contact with an unknown customer stores `customer_salesforce_id`, FK null (BP
  Defaults).
- **AC-1.3.2** `ChangeEvent`: `event_id` varchar(128) with a **database-level** unique
  constraint, `source`, `received_at` (indexed). `FieldChange`: `object_type`, `record_id`,
  `field`, `value` (text, null), `actor`, `source`, `occurred_at`, `status` with choices
  `applied`/`superseded`/`echo`, `event_id`, `created_at`. `Outbox`: `object_type`, `record_id`,
  `operation` (`create`/`update`), `fields` (array of strings, `ArrayField` or JSON list, as
  D §4 says "array"), `state` (`pending`/`sent`/`needs_review`, default `pending`), `attempts`
  (default 0), `next_attempt_at`, `lease_until` (null), `last_error`, `created_at`, `sent_at`
  (null). `SyncState`: `key` primary key, `value` JSONField, `updated_at`. Test: a duplicate
  `event_id` raises `IntegrityError` (use `transaction.atomic` and the real DB, not a mock).
  (BP 1.3.2, D §4, §8)
- **AC-1.3.3** Outbox has a **partial unique constraint** on `(object_type, record_id)` with
  `condition=Q(state="pending")`: a second `pending` row for the same record raises
  `IntegrityError`, but a `sent` or `needs_review` row plus one `pending` row for the same
  record is allowed, and `pending` rows for different records or object types are allowed.
  Test checks it at database level and also inspects `pg_indexes`/`Meta.constraints`. Outbox has
  an index on `(state, next_attempt_at)`; `FieldChange` has an index on
  `(object_type, record_id, occurred_at)`; `ChangeEvent.received_at` is indexed; Contact has
  indexes on `customer`, `customer_salesforce_id`, `email`. Test queries `pg_indexes` (or
  `connection.introspection.get_constraints`) for each. All indexes ship in the same migration
  as their model. (BP 1.3.3, D §4, R "Models and migrations")
- **AC-1.3.4** Model tests live in `src/sync/tests/test_models.py` and cover AC-1.3.1 to
  AC-1.3.3 against real Postgres. The old `src/sync/tests.py` file is removed so `src/sync/tests/`
  (with `__init__.py`) is a package (BP 4.1.1 uses `src/sync/tests/`). Migrations apply on an
  empty database; `uv run python src/manage.py makemigrations --check --dry-run` prints "No
  changes detected" and exits 0. Times are timezone-aware (`USE_TZ`). (BP 1.3.4, R)

**1.4 Django admin** (Slice B). All in `src/sync/admin.py`; tests in
`src/sync/tests/test_admin.py`.

- **AC-1.4.1** `Customer` and `Contact` are registered with `list_display`, `search_fields`
  limited to indexed columns (Customer: `external_id`, `salesforce_id`; Contact: `email`,
  `salesforce_id`, `customer_salesforce_id`, `external_id`; never `name` or `city`),
  `list_select_related` (Contact: `("customer",)`), `ordering`, `show_full_result_count = False`,
  and `raw_id_fields = ("customer",)` on Contact. Tests as a superuser: each changelist,
  add page, change page and a search query return 200; the Contact change page contains no
  `<select` for customer (raw id widget instead); `list_select_related` produces a fixed query
  count for a changelist with several contacts (`django_assert_num_queries` or
  `CaptureQueriesContext`, no N+1). (BP 1.4.1, R "Django admin")
- **AC-1.4.2** `FieldChange` and `ChangeEvent` are registered and read-only:
  `has_add_permission`, `has_change_permission` and `has_delete_permission` all return `False`
  (checked for a superuser). Tests: the add URL returns 403, a POST to the change URL and to
  the delete URL does not alter or remove the row, and the changelist still returns 200 with
  `list_display`, `ordering`, `show_full_result_count = False`. (BP 1.4.2, R)
- **AC-1.4.3** `Outbox` and `SyncState` are registered. `Outbox` is read-only (no add, change,
  delete; add URL 403) and has an admin action `requeue` that only touches rows with
  `state="needs_review"`: they become `pending` with `next_attempt_at` set to now, `attempts`
  reset to 0 and `lease_until` cleared; rows in other states are untouched (test with one
  `needs_review`, one `sent`, one `pending` selected together). The action must not create a
  second pending row for a record that already has one (the partial unique constraint must not
  surface as a 500: skip or report it). Every model is registered: Customer, Contact,
  ChangeEvent, FieldChange, Outbox, SyncState each have a changelist that returns 200.
  (BP 1.4.3, D §6 "needs_review", Q9, R)
  `DEFAULTED`: requeue-collision behaviour (skip with a message) because the brief and D §6 do
  not say.

**1.6 URL layout and serializers** (Slice B)

- **AC-1.6.1** `src/config/urls.py` has `path("api/v1/", include(...))` exactly once, with
  `utils.urls` first and `sync.urls` second, plus `admin/`. The string `api/v1` appears in no
  other file under `src/` (grep test, excluding `tests/`). The old root `health/` route is gone.
  (BP 1.6.1, R "Layout and URLs", user decision)
- **AC-1.6.2** `src/utils/urls.py` has `app_name = "utils"` and one route named `health`
  (`reverse("utils:health")` == `/api/v1/health/`); `src/sync/urls.py` has `app_name = "sync"` and
  an empty `urlpatterns` with a comment listing the planned routes from BP "Endpoints". Neither
  file contains `api/v1`. (BP 1.6.2, R)
- **AC-1.6.3** `src/utils/serializers.py` holds `HealthSerializer` (removed from
  `src/utils/views.py`, which imports it). `src/sync/serializers.py` holds `CustomerSerializer`,
  `ContactSerializer` and `FieldChangeSerializer`, each with explicit `fields` (no `"__all__"`),
  `read_only_fields` covering the server-owned fields (`id`, `external_id`, `salesforce_id`,
  `sync_state`/`updated_at` as applicable), and field names as in API. API uses camelCase
  (`fullName`, `externalId`, `salesforceId`, `updatedAt`, `eventId`, `occurredAt`); BP 7.1.1
  assigns camelCase to phase 7, so for phase 1 the criterion is: field set equals the API
  property set for each schema, with camelCase output names. `DEFAULTED`: render camelCase now
  with explicit `source=` mapping on each field (no new dependency) because API is the contract
  and re-keying later would break tests. Contact `customer` is the portal ID (integer). Do not
  add `sync_state` unless it can be computed without a query per row; if it cannot, leave it out
  and record it as a gap in the ledger for phase 7 (Phoebe decides, Monica checks). Serializers
  must not call `.save()` anywhere (writes go through `apply()` in phase 2; R). (BP 1.6.3, R, API)
- **AC-1.6.4** Tests (`src/utils/tests/test_health.py` updated, `src/sync/tests/test_serializers.py`
  and `src/sync/tests/test_urls.py` new): `GET /api/v1/health/` is 200 with `postgres` and
  `redis` keys; `GET /health/` is 404; `reverse("utils:health")` resolves; every named route in
  the two `urls.py` files reverses; serializer field lists equal the API property sets and each
  server-owned field is read-only (assert via `serializer.fields[name].read_only`). (BP 1.6.4)

**Cross-cutting** (checked at the end of each slice and by Monica)

- **AC-X.1** `uv run python src/manage.py check` exits 0 with no issues. (BP Phase 1 Exit, R)
- **AC-X.2** `uv run python src/manage.py makemigrations --check --dry-run` is clean (exit 0, no
  changes). (BP Exit, R)
- **AC-X.3** `uv run pytest -q` passes: the 6 tests that exist before this task (in
  `src/utils/tests/`) still pass, with `test_health.py` updated only for the new path, plus all
  the new tests. Ledger records the summary line. (BP Exit)
- **AC-X.4** `docs/openapi.yml` field names and lengths agree with the models (BP 1.5.1,
  Chandler; Phoebe must not change `openapi.yml`). Differences found by Phoebe or Ross are
  logged in the ledger for Chandler, not fixed by Phoebe.
- **AC-X.5** No `.env` values appear in the ledger, logs or test output. (CLAUDE.md)
- **AC-X.6** Scope: no views, endpoints, `apply()`, outbox sender or limiter are added; nothing
  committed or staged. (Ledger scope, CLAUDE.md "don't build ahead")

No criterion is `BLOCKED`. The user's "Decided (user)" URL layout is honoured (AC-1.6.1).

## Routing plan
<!-- Joey. Monica checks this table before every dispatch. -->

| # | Agent | Step | Inputs | Done when |
|---|---|---|---|---|
| 1 | agent-phoebe | **Slice A:** 1.2.1 to 1.2.4 (authtoken, DRF defaults, `AllowAny` on `HealthView`, `IsSalesforceIntegration`, `create_integration_user`, `uv add httpx`, env keys in both files, never print `.env`) and 1.3.1 to 1.3.3 (all six models, constraints, indexes, one initial migration in `src/sync/migrations/`). Remove `src/sync/tests.py` in favour of the `tests/` package. | Criteria AC-1.2.x, AC-1.3.x; D §4; API for lengths; R | `manage.py check` and `makemigrations --check --dry-run` clean; migrations apply on empty DB; `uv run pytest -q` still green; ledger Log entry with command output |
| 2 | agent-ross | **Test slice A:** AC-1.2.1 to AC-1.2.4 and AC-1.3.1 to AC-1.3.4 as pytest-django tests (`src/sync/tests/test_models.py`, `src/sync/tests/test_auth.py`, env key-name comparison). DB-level constraint tests use real Postgres. Failures go back to Phoebe with repro. | Slice A code; AC list | Each criterion has a named passing test; pytest summary in ledger; or failures logged with repro (max 3 rounds then Rachel) |
| 3 | agent-phoebe | **Slice B:** 1.6.1 to 1.6.3 (root `api/v1/` mount, `utils/urls.py`, `sync/urls.py`, `utils/serializers.py`, `sync/serializers.py`) and 1.4.1 to 1.4.3 (`src/sync/admin.py`, requeue action). | Criteria AC-1.4.x, AC-1.6.x; slice A merged; R | `manage.py check` clean; `/api/v1/health/` 200, `/health/` 404; admin changelists for all six models return 200; ledger Log entry |
| 4 | agent-ross | **Test slice B:** AC-1.4.1 to AC-1.4.3 (`test_admin.py`, including Django admin checks by hand or client for each changelist/change/search page) and AC-1.6.4 (`test_health.py` update, `test_urls.py`, `test_serializers.py`), plus the `api/v1` grep check AC-1.6.1. Then run all cross-cutting AC-X.1 to X.3. | Slice B code; AC list | Each criterion has a named passing test; full `pytest -q` summary (old 6 + new) in ledger |
| 5 | agent-chandler | Docs: `docs/openapi.yml` vs models (1.5.1, AC-X.4); copy Ross's results into **Test results**; fill **Docs updated**; check `docs/build-plan.md` boxes, mark `defaulted` items and add Defaulted entries (Q14 already there; add requeue-collision and camelCase-now); update wiki/Architecture and design §4 only if models differ from them; store one `[team] T-001 ...` memory lesson | Ledger with passing results | Docs match the code; `openapi.yml` still valid; memory title in **Docs updated** |
| 6 | agent-monica | Verify every AC herself by reading code and running `manage.py check`, `makemigrations --check --dry-run`, `pytest -q`, a grep for `api/v1` and a key-name comparison of `.env` and `.env.example`; write **Verdict**; tick build-plan boxes 1.2 to 1.4 and 1.6 with evidence; list the `DEFAULTED` items for the user | Ledger, code | Verdict written; tell the user to run `/ready-to-commit` |

Rationale: slice A first because models and auth are what everything else (admin, serializers)
imports; slice B depends on A. Two Phoebe to Ross rounds keep each hand-off one coherent slice.
Ross's repro-to-Phoebe loop counts toward the 3-round cap per slice.

`DEFAULTED` items to report (Monica lists at the end): Q14 token auth; contact customer link via
`customer_salesforce_id` with nullable PROTECT FK; requeue skips a record that already has a
pending row; camelCase serializer names now.

## Log
<!-- Append-only. One entry per hand-off. -->

### 2026-11-27 agent-joey: criteria and routing plan
- Read: this ledger, `.claude/rules/agent-team.md`, `.claude/rules/drf.md`, `docs/build-plan.md`
  (phase 1, URL conventions, Defaults), `docs/design.md` §4 and §6 to §9, `docs/openapi.yml`;
  checked `src/config/urls.py` (still has root `health/` route) and the file list under `src/`.
  agent-memory returned no related lessons.
- Wrote one criterion per subtask (1.2.1 to 1.2.4, 1.3.1 to 1.3.4, 1.4.1 to 1.4.3, 1.6.1 to
  1.6.4) plus cross-cutting AC-X.1 to X.6, and a six-step routing table (Phoebe A, Ross, Phoebe B,
  Ross, Chandler, Monica).
- Things Phoebe and Monica should know: `src/sync/tests.py` is a file and must become a
  `tests/` package (BP 4.1.1 plans `src/sync/tests/`); `sync/` has no `urls.py`,
  `serializers.py`, `permissions.py` or `management/` yet; BP 7.1.1 puts camelCase in phase 7, but
  API is the contract, so criterion AC-1.6.3 sets it now as `DEFAULTED`; `sync_state` in the
  record serializers may need a query per row, so Phoebe may leave it out and log the gap.
- No `BLOCKED` items.

### 2026-11-27 agent-monica: plan approved, dispatching Phoebe (slice A)
- Checked Joey's criteria against `docs/build-plan.md` phase 1 and `docs/openapi.yml`: every
  subtask has a testable criterion, and the user's URL layout (AC-1.6.1) is honoured.
- Approved the four DEFAULTED items (Q14 token auth; customer link via `customer_salesforce_id`;
  requeue skips a record that already has a pending row; camelCase serializer names now).
- One addition: Phoebe must not change `docs/openapi.yml`; differences go in the ledger for
  Chandler (AC-X.4).

### 2026-11-27 agent-phoebe: slice A (settings/auth, models, migration)
- **Did:** authtoken + DRF defaults (TokenAuthentication, IsAuthenticated, JSONRenderer kept);
  `SALESFORCE_BASE_URL/TOKEN/RATE_LIMIT` via django-environ (rate default 90); `HealthView` has
  `AllowAny` and `authentication_classes = []`; `uv add httpx`; the three keys appended to `.env`
  and `.env.example` (placeholder values in both; `.env` real values untouched); `IsSalesforceIntegration`;
  `create_integration_user` (`--username`, idempotent, prints the token once); six models with an
  abstract `SyncRecord` base; `0001_initial` migration; `src/sync/tests.py` removed, `src/sync/tests/__init__.py` created.
- **Files:** `src/config/settings.py:46-47,141-153`, `src/utils/views.py:12-16`,
  `src/sync/permissions.py:1`, `src/sync/management/commands/create_integration_user.py:1`,
  `src/sync/models.py:1` (constraint `outbox_one_pending_per_record` at the Outbox Meta;
  indexes `outbox_state_next_idx`, `field_change_record_idx`; `change_event_event_id_uniq`),
  `src/sync/migrations/0001_initial.py`, `pyproject.toml`, `uv.lock`, `.env.example`, `.env`.
- **Ran:** `check` -> "System check identified no issues (0 silenced)"; `makemigrations --check
  --dry-run` -> "No changes detected"; `migrate` -> authtoken 0001-0004 and sync.0001_initial OK;
  `pytest -q` -> "6 passed"; `create_integration_user` run twice -> both printed a 40-char token (idempotent).
- **Notes for Ross:** `Outbox.fields` is a Postgres `ArrayField(CharField(64))`; `ChangeEvent.event_id`
  uniqueness is a `UniqueConstraint` (no extra index needed); `FieldChange.record_id`/`Outbox.record_id` are
  BigIntegerField; `Outbox.next_attempt_at` has no default (callers set it). Running the command in the dev DB left an integration user there.
  `.env` was not printed; only key names were appended.
- **DEFAULTED:** Contact FK null PROTECT plus `customer_salesforce_id`, per build-plan default; `attempts` is PositiveIntegerField; `object_type` max 20, `field` max 64.
- **Open:** none. Hand off to agent-ross.

### 2026-11-27 agent-ross: test slice A
- **Did:** wrote tests for AC-1.2.1 to 1.2.4 and 1.3.1 to 1.3.4 against real Postgres; DB constraint tests use `transaction.atomic()` plus `IntegrityError`; 401 tests use a throwaway view via `@pytest.mark.urls`; health test calls `HealthView` directly, so it survives the Slice B URL move; env check compares key names only.
- **Files:** `src/sync/tests/test_models.py`, `src/sync/tests/test_auth.py` (no app code changed).
- **Ran:** `uv run pytest -q` -> "36 passed in 0.59s"; `check` clean; `makemigrations --check --dry-run` -> "No changes detected".
- **Open:** none; no failures. Hand off to the next step (routing step 3, agent-phoebe, Slice B). Phoebe note: `test_health.py` in `src/utils/tests/` still uses `/health/`; update it for `/api/v1/health/`.

### 2026-11-27 agent-phoebe: slice B (URL layout, serializers, admin)
- **Did:** `config/urls.py` mounts `api/v1/` once (utils.urls, then sync.urls) plus `admin/`; root `health/` removed.
  `utils/urls.py` (`app_name="utils"`, route `health`), `sync/urls.py` (`app_name="sync"`, empty, planned routes in a comment).
  `HealthSerializer` moved to `utils/serializers.py`. `sync/serializers.py`: Customer/Contact/FieldChange ModelSerializers,
  explicit fields, camelCase via `source=`, server-owned fields read-only, Contact `customer` is the integer portal ID, no `.save()`.
  Admin for all six models; `ReadOnlyAdmin` base (add/change/delete False, `show_full_result_count=False`) for ChangeEvent,
  FieldChange, Outbox; Outbox `requeue` action touches only `needs_review`, skips with a warning message any row whose
  record already has a pending row. `test_health.py` path changed to `/api/v1/health/`.
- **Files:** `src/config/urls.py:18-23`, `src/utils/urls.py:1`, `src/sync/urls.py:1`, `src/utils/serializers.py:1`,
  `src/utils/views.py:1-8`, `src/sync/serializers.py:1`, `src/sync/admin.py:1` (requeue at `OutboxAdmin.requeue`),
  `src/utils/tests/test_health.py:9`.
- **Ran:** `check` -> "no issues (0 silenced)"; `makemigrations --check --dry-run` -> "No changes detected";
  `pytest -q` -> "36 passed in 0.59s"; `grep -rn "api/v1" src --include='*.py'` -> only `src/config/urls.py`;
  `reverse("utils:health")` -> `/api/v1/health/`.
- **Gap for phase 7:** `syncState` left out of Customer/Contact serializers (needs an outbox lookup per row; add with an
  annotation/prefetch on the list queryset). `FieldChange.record_id`/`object_type` are not in the API schema, so not serialized.
- **For Chandler:** openapi `Customer`/`Contact` require `syncState` (readOnly) but serializers omit it until phase 7. No other openapi differences found.
- **DEFAULTED:** requeue collision = skip with a warning message; camelCase names now via `source=`; Contact `customer`
  writable PrimaryKeyRelatedField (default queryset) since the PATCH goes through `apply()` in phase 2/7.
- **Notes for Ross:** requeue uses `row.save(update_fields=...)` on Outbox (not a synced record, so allowed). Admin not browser-tested by me.
- **Open:** none. Hand off to agent-ross.

### 2026-11-27 agent-ross: test slice B and cross-cutting
- **Did:** admin tests for all six models (changelist, search, change page, add page, read-only perms, POST change/delete refused, requeue with collision), serializer tests against openapi property sets, URL/health/grep tests, X.1 to X.3 and X.5.
- **Files:** `src/sync/tests/test_admin.py`, `src/sync/tests/test_serializers.py`, `src/sync/tests/test_urls.py` (no app code changed).
- **Ran:** `uv run pytest -q` -> "77 passed in 3.51s"; `check` clean; `makemigrations --check --dry-run` -> "No changes detected".
- **Open:** none; no failures (round 1 of 3 for slice B passed). Hand off to agent-chandler.

### 2026-11-27 agent-chandler: docs, openapi check, build-plan, memory
- **Did:** re-ran `uv run pytest -q` myself (matches Ross's summary: see Ran). Compared `docs/openapi.yml` with `src/sync/models.py` and `serializers.py`: field names, lengths and the Contact `customer` integer already agreed; added `maxLength` to FieldChange `field` (64), `actor` (255), `source` (40) and `eventId` (128), marked `syncState` as "Added in phase 7; not returned yet" (kept in SyncFields), and noted that `object_type`/`record_id`, Outbox and SyncState are not exposed. Updated every `/health/` mention to `/api/v1/health/`, documented the URL conventions, token auth and `create_integration_user`. Rewrote `design.md` §4 to the built columns. Moved both Ross entries from "Test results" into this Log in time order (text unchanged). Added `defaulted` markers (1.2.2, 1.3.1, 1.4.3, 1.6.3) and three "Defaulted decisions to review" entries in `docs/build-plan.md`. Stored the memory lesson.
- **design.md §4 differences from the earlier text (fixed to match the models):** added lengths the old text left open (`object_type` 20, `field` 64, `actor` 255, `source` 40, `status` 12, `operation` 10, `state` 12, `key` 64, array items 64); `record_id` is bigint, not a FK; `Outbox.fields` is a Postgres ArrayField; named the constraints and indexes (`change_event_event_id_uniq`, `outbox_one_pending_per_record`, `outbox_state_next_idx`, `field_change_record_idx`); defaults (`field_meta={}`, `customer_salesforce_id=""`, `attempts=0`, `state=pending`); `next_attempt_at` has no default; Contact FK related name `contacts`.
- **Files:** `docs/openapi.yml`, `docs/design.md`, `docs/build-plan.md`, `README.md`, `CLAUDE.md` (Layout section only), this ledger. `wiki/Architecture.md` names no paths, so unchanged.
- **Ran:** `uv run pytest -q` -> "77 passed in 3.47s" (matches Ross); `manage.py check` -> "no issues (0 silenced)"; `bash scripts/build-wiki.sh "$(mktemp -d)"` -> "Built 7 pages"; `ruby YAML.load_file docs/openapi.yml` -> parses.
- **Drift report (checkboxes vs ledger):** none. 1.2 to 1.4 and 1.6 are still `[ ]` with **In progress**, correct until Monica verifies; 1.5.1 is `[ ]` (my work is done, Monica ticks it). Status table row for Phase 1 still reads "Building 1.2, 1.3, 1.4, 1.6", Monica updates it. Ledger evidence exists for every AC (77 passed).
- **Open:** (1) OpenAPI validation: no validator installed; I only confirmed the YAML parses (Ruby `YAML.load_file`). Monica to run a real OpenAPI 3.1 validator if she wants one. (2) `.claude/rules/drf.md` and the skill already carry the URL convention, so no change. (3) Phase 1 took 2 slices, each passing Ross's tests in round 1 of 3.
- Hand off to agent-monica.

### 2026-11-27 agent-ross: follow-up, openapi no longer lists syncState
- **Did:** `syncState` was removed from `SyncFields` in `docs/openapi.yml` (user option A), which broke `test_spec_parser_sanity`. It now asserts `{"id", "name", "externalId", "createdBy"}`. Removed the now-dead `omitted={"syncState"}` parameter from `test_ac_1_6_3_field_set_equals_openapi`, which now compares serializer fields to the schema exactly. Kept `test_ac_1_6_3_sync_state_absent_on_purpose`.
- **Files:** `src/sync/tests/test_serializers.py` only.
- **Ran:** `uv run pytest -q` -> "92 passed in 3.93s".
- **Open:** none.

## Test results
<!-- Ross: the pytest summary line, plus each AC → test name → pass/fail. -->

**Slice A:** `uv run pytest -q` -> `36 passed in 0.59s` (6 old + 30 new). `manage.py check` -> "no issues (0 silenced)"; `makemigrations --check --dry-run` -> "No changes detected", exit 0.

| AC | Test (prefix `test_ac_...`) | Result |
|---|---|---|
| 1.2.1 | `test_ac_1_2_1_token_rows_can_be_created` | pass |
| 1.2.2 | `test_ac_1_2_2_drf_defaults`, `_no_token_is_401_not_403`, `_bad_token_is_401`, `_good_token_is_200`, `_health_needs_no_token` | pass |
| 1.2.3 | `test_ac_1_2_3_permission_allows_group_member`, `_denies_portal_user`, `_denies_anonymous`, `_command_creates_one_user_group_token`, `_command_rerun_is_idempotent` | pass |
| 1.2.4 | `test_ac_1_2_4_httpx_importable_and_locked`, `_rate_limit_is_int_with_default_90`, `_env_and_example_have_same_key_names` | pass |
| 1.3.1 | `test_ac_1_3_1_defaults`, `_duplicate_external_id_raises[both models]`, `_null_salesforce_ids_coexist`, `_same_salesforce_id_raises`, `_contact_same_salesforce_id_raises`, `_delete_customer_with_contact_protected`, `_contact_without_customer_keeps_sf_id` | pass |
| 1.3.2 | `test_ac_1_3_2_duplicate_event_id_raises`, `_field_change_and_outbox_defaults` | pass |
| 1.3.3 | `test_ac_1_3_3_second_pending_raises`, `_non_pending_plus_one_pending_allowed`, `_other_record_or_type_allowed`, `_constraint_is_partial_in_db`, `_indexes_exist` | pass |
| 1.3.4 | `test_ac_1_3_4_times_are_timezone_aware`; package + migrations: pytest collects `src/sync/tests/`, X.2 clean | pass |
| X.1 | `manage.py check` | pass |
| X.2 | `makemigrations --check --dry-run` | pass |
| X.3 | `pytest -q` (36 passed; the 6 old tests still pass) | pass |

Notes: the rate-limit default is checked on the settings source (`env.int(..., default=90)`) because `.env` sets the key, so a reload would not show the default.

**Slice B:** `uv run pytest -q` -> `77 passed in 3.51s` (36 before + 41 new). `manage.py check` -> "System check identified no issues (0 silenced)"; `makemigrations --check --dry-run` -> "No changes detected", exit 0. `.env` vs `.env.example` key names (names only): identical.

| AC | Test (prefix `test_ac_...`) | Result |
|---|---|---|
| 1.4.1 | `1_4_1_add_page_200[Customer,Contact]`, `_search_fields_are_indexed_columns_only`, `_options_set`, `_contact_change_page_uses_raw_id_widget`, `_contact_changelist_query_count_is_fixed` (same count for 3 and 10 contacts) | pass |
| 1.4.2 | `1_4_2_add_page_403_for_read_only[ChangeEvent,FieldChange,Outbox]`, `_perms_false_for_superuser[x3]`, `_post_change_and_delete_leave_row_untouched[x3]` | pass |
| 1.4.3 | `1_4_3_every_model_registered_changelist_search_change_200[all 6]`, `_requeue_action` (needs_review requeued; sent/pending untouched; colliding row skipped with warning; 200 not 500) | pass |
| 1.6.1 | `1_6_1_api_v1_defined_once_in_root_and_nowhere_else`, `_root_includes_utils_then_sync` | pass |
| 1.6.2 | `1_6_2_reverse_health`, `_app_names` | pass |
| 1.6.3 | `1_6_3_field_set_equals_openapi[x3]` (syncState omitted on purpose, phase 7), `_sync_state_absent_on_purpose[x2]`, `_server_owned_fields_read_only[x3]`, `_explicit_fields_no_all`, `_output_camel_case_and_customer_is_int`, `_serializers_never_call_save` | pass |
| 1.6.4 | `1_6_4_health_200_with_keys`, `_old_health_route_404`, `_every_named_route_reverses`; `utils/tests/test_health.py` (4 tests, new path) | pass |
| X.1 / X.2 / X.3 | check / makemigrations --check --dry-run / pytest -q (77 passed) | pass |
| X.5 | key-name-only diff of `.env` vs `.env.example`: no difference; no values printed | pass |

Note: no PyYAML installed, so `test_serializers.py` parses `docs/openapi.yml` with a small indent-based reader (resolves `allOf` refs).

## Docs updated
<!-- Chandler: each file changed and why; the agent-memory title stored. -->

- `docs/openapi.yml`: added maxLength on FieldChange `field`/`actor`/`source`/`eventId`; `syncState` marked "added in phase 7; not returned yet"; note on unexposed fields/tables (AC-X.4, 1.5.1).
- `docs/design.md`: status line, §2 (health row, Django web/Postgres rows, URL conventions and auth paragraph), §3 done line, §4 rewritten to the built columns, constraint and index names.
- `docs/build-plan.md`: `defaulted` markers on 1.2.2, 1.3.1, 1.4.3, 1.6.3; three new "Defaulted decisions to review" entries (no boxes ticked).
- `README.md`: health path, "API token for Salesforce" section (`create_integration_user`), Layout lines.
- `CLAUDE.md` (Layout section only): src/config, src/sync, src/utils lines.
- This ledger: moved both Ross entries into Log; Log entry above.
- Unchanged: `wiki/*.md` (no paths named), `.claude/rules/drf.md` and the skill (already carry the URL convention).
- agent-memory: `[team] T-001 phase 1 foundations built` (id 16083).

## Verdict
<!-- Monica: each AC → verified how → ✅/❌; then done / sent back to <agent>. -->

**Verified by Monica on 2026-11-27** by reading the code and re-running the commands herself
(not from the agents' reports). Result: **done**. Nothing sent back.

Commands run: `manage.py check` (no issues), `makemigrations --check --dry-run` (No changes
detected), `pytest -q` (**77 passed**: 6 existing + 71 new), `openapi-spec-validator` on
`docs/openapi.yml` (valid), a grep for `api/v1` in `src/`, a key-name-only diff of `.env` and
`.env.example`, and `pg_indexes` on the real dev database.

| AC | How verified | |
|---|---|---|
| 1.2.1 authtoken installed, migrates | migrations applied; Token tests pass | ✅ |
| 1.2.2 Token auth + IsAuthenticated default; health is AllowAny | settings read; 401 tests pass; `/api/v1/health/` is 200 without a token | ✅ |
| 1.2.3 `salesforce-integration` group, permission class, idempotent command | tests for allow/deny and for running the command twice pass | ✅ |
| 1.2.4 httpx, SALESFORCE_* settings, same `.env` keys | httpx imports; key-name diff `same`; default 90 in settings source | ✅ |
| 1.3.1 Customer, Contact | models read; uniqueness and PROTECT tests pass against real Postgres | ✅ |
| 1.3.2 ChangeEvent, FieldChange, Outbox, SyncState | models read; `pg_indexes` shows `change_event_event_id_uniq` | ✅ |
| 1.3.3 Partial unique + indexes | `pg_indexes` shows `outbox_one_pending_per_record … WHERE state = 'pending'`, `outbox_state_next_idx`, `field_change_record_idx` | ✅ |
| 1.3.4 Model tests, tests package, clean migrations | `tests/` package exists, tests pass, no pending migrations | ✅ |
| 1.4.1 Customer/Contact admin | `admin.py` read: indexed-only search, `list_select_related`, raw-id, no full count; N+1 test passes | ✅ |
| 1.4.2 FieldChange, ChangeEvent read-only | `ReadOnlyAdmin` returns False for add/change/delete; tests pass (403, rows unchanged) | ✅ |
| 1.4.3 Outbox read-only + requeue; all six registered | requeue test covers needs_review / sent / pending and the collision case | ✅ |
| 1.6.1 `api/v1/` only in `config/urls.py` | grep: one hit, `src/config/urls.py:23`; old `/health/` is 404 | ✅ |
| 1.6.2 `app_name` + `urls.py` per app | `reverse("utils:health")` is `/api/v1/health/`; `sync.urls` empty with route comment | ✅ |
| 1.6.3 `serializers.py` per app | field sets equal the openapi schemas; camelCase; server fields read-only; no `.save(` | ✅ |
| 1.6.4 Tests | health, urls and serializer tests pass | ✅ |
| 1.5.1 openapi matches models | validator passes; serializer tests compare against it | ✅ |
| X.1–X.6 | all as above; no views or `apply()` added; nothing staged | ✅ |

**Known gap (not a failure):** `syncState` is in `openapi.yml` but not yet returned by the
customer and contact serializers. It needs a query per row; phase 7 adds it with an
annotation. The spec says so ("added in phase 7"), and a test asserts it is absent.

**DEFAULTED decisions for the user to review** (also in `docs/build-plan.md`, unticked):
1. Q14: DRF token per portal user.
2. A contact whose customer isn't known yet keeps `customer_salesforce_id`; the FK is nullable
   with PROTECT.
3. The Outbox `requeue` action skips a record that already has a pending row, with a warning.
4. camelCase serializer field names now (the contract), not in phase 7.
5. `record_id` on Outbox and FieldChange is a BigIntegerField; `attempts` is a
   PositiveIntegerField; `object_type` 20 and `field` 64 characters.

**Side effect for the user:** `create_integration_user` was run while building, so the dev
database now has a `salesforce-integration` user with a token. No token is in any file.
