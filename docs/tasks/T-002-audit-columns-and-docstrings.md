# T-002: created_by / updated_by on records, exposed in the API; model docstrings

**Status:** done
**Owner:** agent-monica
**Requested:** 2026-11-27 by the user: "Are we saving created_by?" then "Go with option B and C. I also want you to update the docstring in the models."
(B = add `created_by` / `updated_by` columns to Customer and Contact. C = also expose them in the API as `createdBy` / `updatedBy`.)

**Scope:** `docs/build-plan.md` task **1.7**. Small change, so Monica wrote the criteria and the
routing plan herself instead of dispatching Joey (recorded here as a deliberate shortcut).
Out of scope: setting the columns (that's `apply()`, phase 2, build-plan 2.2.4), views, any
other model change.
**Contract (already updated by Monica):** `docs/openapi.yml` `SyncFields` and `docs/design.md` §4.

## Acceptance criteria
- **AC-1.7.1** `Customer` and `Contact` (via the shared `SyncRecord` base in
  `src/sync/models.py`) have `created_by` and `updated_by`: `CharField(max_length=255,
  blank=True, default="")`. One new migration (`0002_...`); `makemigrations --check --dry-run`
  is clean afterwards. Creating a record without them stores `""`. (BP 1.7.1, D §4)
  `DEFAULTED`: empty string rather than NULL, as Django recommends for text fields.
- **AC-1.7.2** `CustomerSerializer` and `ContactSerializer` return `createdAt`, `createdBy`,
  `updatedAt`, `updatedBy`, **all read-only**, with the names in `docs/openapi.yml`. The
  serializer field sets still equal the openapi schema property sets (except `syncState`, still
  deferred to phase 7). A `PATCH`-style write payload containing them cannot change them
  (they're read-only). The admin shows `created_by` / `updated_by` read-only on the Customer and
  Contact change pages and lists them in `list_display`. (BP 1.7.2, API)
- **AC-1.7.3** `src/sync/models.py` has a docstring on **every** model class (`SyncRecord`,
  `Customer`, `Contact`, `ChangeEvent`, `FieldChange`, `Outbox`, `SyncState`) saying what it is
  for, its key constraints and indexes by name, and the design section that describes it; and a
  short comment or `help_text`-free note on each non-obvious field (`field_meta` keys,
  `Outbox.fields`, `lease_until`, `next_attempt_at`, `customer_salesforce_id`, `record_id`,
  `status` choices). No behaviour change, and no `help_text` (that would change migrations).
  (BP 1.7.3)
- **AC-1.7.4** Tests (extend the existing files; names `test_ac_1_7_...`): columns exist with
  type, length 255 and default `""` (via `connection.introspection` or model `_meta`); a created
  record has `""`; the serializers return the three new fields and mark them read-only; the
  openapi comparison test still passes; the admin changelist and change page still return 200
  with the new columns. (BP 1.7.4)
- **AC-X.1** `manage.py check` clean. **AC-X.2** `makemigrations --check --dry-run` clean.
  **AC-X.3** `pytest -q` passes (77 before + the new ones). **AC-X.4** `docs/openapi.yml` still
  validates and matches the serializers. **AC-X.5** no `.env` values printed. **AC-X.6** no views,
  no `apply()`, nothing committed or staged; Phoebe does not edit `docs/`.

## Routing plan
| # | Agent | Step | Done when |
|---|---|---|---|
| 1 | agent-phoebe | AC-1.7.1 to 1.7.3: columns + migration, docstrings, serializers, admin | check, makemigrations --check and pytest pass; Log entry |
| 2 | agent-ross | AC-1.7.4 and AC-X.x: tests; failures go back to Phoebe (max 3 rounds) | named passing tests; pytest summary in Test results |
| 3 | agent-chandler | Docs: README/CLAUDE.md/wiki if they mention fields; check design §4 and openapi match the built models; defaulted note; one `[team] T-002` memory lesson | Docs updated filled; no drift |
| 4 | agent-monica | Verify every AC; tick build-plan 1.7.x; write Verdict | Verdict written |

## Log
### 2026-11-27 agent-monica: criteria and routing written; contract updated
- Updated `docs/openapi.yml` (`createdAt`, `createdBy`, `updatedBy` on `SyncFields`; validates),
  `docs/design.md` §4 (new row), and `docs/build-plan.md` (task 1.7, and 2.2.4 now also sets
  the columns). Marked 1.7.1 to 1.7.4 in progress.
- Dispatching Phoebe for step 1.

### 2026-11-27 agent-phoebe: step 1 (AC-1.7.1 to 1.7.3) done
**Did**
- `created_by` / `updated_by` (`CharField(255, blank=True, default="")`) on `SyncRecord`; migration `0002_audit_columns` generated and applied.
- Docstrings on all 7 models (named constraints/indexes, design sections); comments on `field_meta` keys, `created_by`/`updated_by` (set by apply(), empty until phase 2), `customer_salesforce_id`, `record_id`, `status`, `Outbox.fields`/`state`/`next_attempt_at`/`lease_until`. No `help_text`; makemigrations is clean.
- Serializers return read-only `createdAt`, `createdBy`, `updatedAt`, `updatedBy` (no `syncState`).
- Admin: `created_by`/`updated_by` in `list_display` and, with `created_at`/`updated_at`, in `readonly_fields` for Customer and Contact. `search_fields` unchanged.
**Files**
- `src/sync/models.py:8` (SyncRecord, columns ~:26), `src/sync/migrations/0002_audit_columns.py`
- `src/sync/serializers.py:9`, `src/sync/admin.py:21`
**Ran**
- `manage.py check`: no issues. `makemigrations --check --dry-run`: No changes detected. `pytest -q`: 77 passed.
**Open**
- For Ross: no existing test broke. Design cites in docstrings: FieldChange §4/§7, Outbox §6/§10, SyncState §4/§10. Hand off to agent-ross.

### 2026-11-27 agent-ross: step 2 (AC-1.7.4, AC-X.1 to X.5) done, round 1 of 3, no failures
**Did**
- Added 15 `test_ac_1_7_*` tests (models, serializers, admin, docstrings) in the existing files.
**Files**
- `src/sync/tests/test_models.py`, `src/sync/tests/test_serializers.py`, `src/sync/tests/test_admin.py`
**Ran**
- `uv run pytest -q`: 92 passed. `manage.py check`: no issues. `makemigrations --check --dry-run`: No changes detected. `.env` vs `.env.example` key names: match.
**Open**
- None. Hand off to agent-chandler.

### 2026-11-27 agent-chandler: step 3 (docs) done
**Did**
- Compared design §4 and openapi `SyncFields` with the built code: no drift. Checked README, CLAUDE.md, wiki: nothing to fix.
- build-plan: 1.7.1 wording and `defaulted`; added the unticked defaulted decision. Stored one memory lesson.
**Files**
- `docs/build-plan.md`, this ledger
**Ran**
- Read-only greps for created_by/updated_by/createdBy/updatedBy across docs. No openapi change, so no re-validation needed.
**Open**
- I did not re-run pytest or the wiki build myself. Monica: please run `uv run pytest -q` and `bash scripts/build-wiki.sh "$(mktemp -d)"`. Hand off to agent-monica.

## Test results
`uv run pytest -q`: **92 passed in 3.90s** (77 before + 15 new).

| AC | Test | Result |
|---|---|---|
| 1.7.1 / 1.7.4 columns: CharField, 255, blank, default "", not null, no unique; real varchar(255) | `test_models.py::test_ac_1_7_audit_columns_meta_and_db` (x4) | pass |
| 1.7.1 created record stores "" | `test_ac_1_7_created_record_stores_empty_strings` (x2) | pass |
| 1.7.1 no accidental unique | `test_ac_1_7_two_records_same_audit_value_allowed` | pass |
| 1.7.2 four fields present and read-only, no syncState | `test_serializers.py::test_ac_1_7_audit_fields_present_and_read_only` (x2) | pass |
| 1.7.2 output "" for both | `test_ac_1_7_output_has_empty_audit_strings` | pass |
| 1.7.2 write payload cannot set them | `test_ac_1_7_write_payload_cannot_set_audit_fields` | pass |
| 1.7.2 openapi field-set equality | `test_ac_1_6_3_field_set_equals_openapi` (existing) | pass |
| 1.7.2 admin list_display, read-only text on change page, 200s, search unchanged | `test_admin.py::test_ac_1_7_admin_lists_and_shows_audit_columns_read_only` (x2), `test_ac_1_7_search_fields_unchanged`; existing read-only-model tests | pass |
| 1.7.3 docstrings cite "§", no help_text | `test_models.py::test_ac_1_7_3_model_docstrings_cite_design_and_no_help_text` | pass |
| X.1 | `manage.py check`: no issues | pass |
| X.2 | `makemigrations --check --dry-run`: No changes detected | pass |
| X.3 | pytest 92 passed | pass |
| X.4 | openapi comparison test passes (X.4/X.6 are Monica's to verify) | pass |
| X.5 | `.env` vs `.env.example` compared by key name only: keys match; no values printed | pass |

Note: psycopg3 reports varchar length in `display_size`, not `internal_size`; the test uses that.

## Docs updated
- `docs/design.md` §4 and `docs/openapi.yml` `SyncFields`: checked against the built models, serializers, migration `0002_audit_columns` and admin. They match (varchar(255), empty default, `createdBy`/`updatedBy` read-only, set by `apply()`). No change.
- `README.md`, `CLAUDE.md`, `wiki/*.md`: no field lists or mentions of the audit columns. No change.
- `docs/build-plan.md`: 1.7.1 now names migration `0002_audit_columns` and carries `defaulted`; added unticked "created_by / updated_by default to an empty string, not NULL (T-002)" under "Defaulted decisions to review". No boxes ticked. 1.7.2 to 1.7.4 wording matches the build.
- Wiki build check not re-run (no wiki files changed; no shell tool for it). Test results: not re-run by me (no shell for pytest beyond read-only); Ross's 92 passed is taken from his entry. Monica should confirm with `uv run pytest -q`.
- agent-memory: `[team] T-002 audit columns created_by updated_by`

## Verdict
**Verified by Monica on 2026-11-27** by running the commands and reading the code herself.
Result: **done**. Nothing sent back.

Commands run: `manage.py check` (no issues), `makemigrations --check --dry-run` (No changes
detected), `pytest -q` (**92 passed**: 77 before + 15 new), `openapi-spec-validator` (valid),
`bash scripts/build-wiki.sh` (7 pages), key-name diff of `.env` and `.env.example` (same), and a
query of `information_schema` on the real database.

| AC | How verified | |
|---|---|---|
| 1.7.1 columns + migration | `0002_audit_columns.py` adds `created_by` and `updated_by` to both models; the real database has all four as `varchar(255) NOT NULL` | ✅ |
| 1.7.2 API and admin | all four fields (`createdAt`, `createdBy`, `updatedAt`, `updatedBy`) present and read-only in both serializers; admin lists them read-only; openapi comparison test passes | ✅ |
| 1.7.3 docstrings | all 7 model classes have a docstring citing a design section; `help_text` count is 0, so no migration impact | ✅ |
| 1.7.4 tests | 15 tests named `test_ac_1_7_*` pass; Ross's first-run failures were his own test (psycopg3 `display_size`), fixed by him | ✅ |
| X.1–X.5 | as above | ✅ |
| X.6 scope | no `apply()`, no new views, nothing staged | ✅ |

**Note:** the empty-string default is applied by Django when it saves; the database column has
no default of its own (normal for Django). Anything writing with raw SQL would have to supply
the values. `apply()` (phase 2) is the only writer, so this is fine.

**Not done yet, on purpose:** nothing sets `created_by` / `updated_by`. `apply()` does that in
phase 2 (build-plan 2.2.4, updated to say so), so the columns are empty today.

**DEFAULTED for the user to review** (in `docs/build-plan.md`, unticked): `created_by` and
`updated_by` default to an empty string, not NULL.
