# T-003: `apply()` — the sync engine's rules core

**Status:** done
**Owner:** agent-monica
**Requested:** 2026-11-27 by the user: "Phase 2: build apply() — the Change dataclass, the
one-transaction apply() function (dedupe via ChangeEvent, record lock, echo detection,
per-field ordering with FieldChange history, field_meta updates, created_by/updated_by), the
outbox write with the sync-loop rule, and the concurrency tests. Follow docs/build-plan.md
phase 2 (tasks 2.1-2.4) and the .claude/skills/sync-engine and .claude/skills/sync-testing
skills exactly as written."

**Scope:** `docs/build-plan.md` phase 2, tasks **2.1, 2.2, 2.3, 2.4** exactly as written there,
built per `.claude/skills/sync-engine/SKILL.md` §1 and tested per
`.claude/skills/sync-testing/SKILL.md` §1–2.
Out of scope: the webhook view (phase 3), `FakeSalesforce`/scenario builders (phase 4), the
outbound task/limiter/probe/catch-up (phases 5–6), the portal API views (phase 7). `apply()`
itself is pure Postgres logic with no network and no Salesforce client — phases 3 and 5+ call
into it, this task only builds it.

**Sources:** `docs/design.md` §5–§9 (the decided rules), the wiki Architecture page's
`apply(change)` and "How the brief's rules are met" sections, `.claude/skills/sync-engine/SKILL.md`
(the concrete algorithm, already written out — Phoebe should follow it closely, not re-derive
it), `.claude/skills/sync-testing/SKILL.md` (clock injection, `TransactionTestCase`), `src/sync/models.py`
(the six models already built in T-001/T-002), `.claude/rules/drf.md`.

## Acceptance criteria
<!-- Joey drafts; Monica approves. Each one testable. Cite the brief or design section. -->

Files: `src/sync/services.py` (new: `Change`, `Result`, `apply()` and helpers),
`src/sync/tests/test_apply.py` (new, Ross), `src/config/settings.py` + `.env` + `.env.example`
(new setting). "Skill §1" = `.claude/skills/sync-engine/SKILL.md` §1. Criteria hold Phoebe to
the skill's BEHAVIOUR, not its exact code. Every apply() test calls the function directly with
an explicit `occurred_at` (no `timezone.now()` for ordering; sync-testing §1).

- **AC-2.1.1** (build-plan 2.1.1; design §8; skill §1 "The Change input")
  `src/sync/services.py` defines a frozen `Change` dataclass with exactly: `source`, `event_id`,
  `object_type`, `record_ref`, `occurred_at`, `actor`, `fields`, `operation`; and a `Result`
  with `outcome` (`applied` | `no_change` | `duplicate` | `echo`) and `applied_fields`.
  Test: build a `Change`; assigning to an attribute raises `FrozenInstanceError`; `apply()`
  returns a `Result` with those outcome values. `catch-up` is not a separate behaviour: it
  must be treated exactly like `salesforce` everywhere below (criteria AC-2.2.x, AC-2.3.2).
- **AC-2.1.2a** (2.1.2 + "Done when"; design §8; skill §1 transaction)
  Calling `apply()` twice with the same `event_id` gives exactly 1 `ChangeEvent` row, the second
  call returns `outcome="duplicate"`, and the second call writes no `FieldChange` row, changes
  no record column and creates no outbox row. Test with one salesforce and one portal change.
- **AC-2.1.2b** (2.1.2; design §8; skill §1 transaction + `_lock_record`)
  The whole `apply()` runs in one `transaction.atomic()`. Test: force an exception after the
  record save (patch `_upsert_outbox_row` to raise) and assert no `ChangeEvent`, `FieldChange`,
  record change or `Outbox` row survives. (This also covers the 2.3 "commit together, or
  neither" done-when.)
- **AC-2.1.2c** (2.1.2; skill §1 `_lock_record`)
  Record lookup: source `salesforce`/`catch-up` finds by `salesforce_id=record_ref`; if not
  found and `operation="create"`, creates a new row with that `salesforce_id`, using
  `select_for_update().get_or_create` inside the open transaction. Source `portal` looks up by
  pk and never creates. Test: salesforce create makes one Customer and one Contact row (both
  object types); a second salesforce update for the same id finds the same row (count stays 1).
  Treat as decided (not open). An unknown portal pk or an unknown salesforce id with
  `operation="update"` raises a clear error (does not silently create); the transaction rolls
  back so even the `ChangeEvent` does not persist (so a retry is not misreported `duplicate`).
  DEFAULTED: raise `DoesNotExist`-style error for these because design is silent on an update
  to an unknown record and swallowing it would lose data.
- **AC-2.1.2d** (2.4 intent; skill "Concurrency (2.4)")
  The lock is `SELECT ... FOR UPDATE` taken before the changed-field check and the
  `field_meta` read, so the changed-field comparison happens inside the lock (2.2.1). Proven
  behaviourally by AC-2.4.1/2.4.2, not by inspecting SQL.
- **AC-2.2.1** (2.2.1; design §7, Q2; skill §1 `_changed_fields`)
  Salesforce/catch-up source: a field is considered changed only if its incoming value differs
  from `field_meta[field]["sf_value"]` (last value received FROM Salesforce), not from the
  current column. Portal source: every field in `change.fields` is a candidate. This check must
  hold regardless of whether a webhook carries only the changed fields (Q2's default
  assumption, revised 2026-11-27 to match the brief's sample) or the whole record (Q2's
  fallback case) — test with the **fallback, whole-record shape**, since that's what exercises
  the filter (a changed-fields-only payload has nothing stale to filter out): portal sets
  `city=X` (newer); a later whole-record webhook with the old `city` and a new `phone`
  yields `applied_fields == ["phone"]` only; a webhook whose values all equal `sf_value`
  returns `outcome="no_change"` and leaves the record unchanged. Fields with no `sf_value`
  yet count as changed.
- **AC-2.2.2** (2.2.2; design §7 echo; skill §1 "Echo")
  A `setting` for the integration user is added: `SALESFORCE_INTEGRATION_USER` in
  `src/config/settings.py` next to `SALESFORCE_TOKEN`, and in `.env` and `.env.example`
  with a placeholder value (same key in both; never print `.env` values). Source
  `salesforce`/`catch-up` with `actor == settings.SALESFORCE_INTEGRATION_USER` returns
  `outcome="echo"`, writes one `FieldChange` per field with `status=echo`, touches no record
  column (including `field_meta`, `updated_by`), and creates 0 outbox rows. The `ChangeEvent`
  row IS kept. Test with `override_settings`. DEFAULTED: setting name
  `SALESFORCE_INTEGRATION_USER` and a default placeholder because the brief does not name it.
  A portal-source change whose actor equals the integration user string is NOT an echo
  (echo applies only to salesforce/catch-up).
- **AC-2.2.3** (2.2.3; design §7; skill §1 loop)
  For every field in the changed set, always write one `FieldChange` (fields: `object_type`,
  `record_id`, `field`, `value`, `actor`, `source`, `occurred_at`, `event_id`, `status`).
  `status=applied` if `occurred_at` is strictly newer than the stored `field_meta[f]["at"]` (or
  no stored time), else `superseded`; an exact tie is `applied` when source is
  `salesforce`/`catch-up` and `superseded` when source is `portal`. The stored `at` is an ISO
  string in JSON, so Phoebe must parse it back to an aware datetime before comparing (the skill
  snippet compares a datetime with the raw string value; follow its behaviour, not that code).
  Tests (the four 2.2 "Done when" tests): (1) the brief's scenario end to end: portal sets
  `city` at t2, then a salesforce webhook **in the whole-record shape** (stale `city`, new
  `phone`, occurred_at t3 > t2 but `city` equal to old sf_value — the brief's own scenario is
  easiest to state this way, even though a real webhook would likely send only `phone`) leaves
  the portal `city`, applies `phone`, and
  creates an outbox row only for the portal edit (explicit end-to-end test, cites brief /
  design §7 / Architecture); (2) an older event after a newer one leaves the column
  unchanged, writes a `superseded` FieldChange, and returns `no_change`; (3) two fields in one
  change both applied, 2 `FieldChange` rows; (4) a tie goes to Salesforce. Superseded and echo
  fields still get a FieldChange row.
- **AC-2.2.4** (2.2.4; design §6/§7; skill §1 loop, `created_by`)
  On each applied field `field_meta[f] = {"at": iso, "source", "by": actor, "sf_value"}`:
  `sf_value` is set to the new value when source is `salesforce`/`catch-up`, and left unchanged
  (kept if present) when source is `portal`. A superseded field does NOT change `field_meta`.
  `updated_by = change.actor` on every change that applied at least one field (and not on
  `no_change`/`echo`/`duplicate`). `created_by` is set only on creation, to the creating
  actor, and never overwritten by later applied changes. NOTE for Phoebe: the skill's
  `record._state.adding` check is not reliable after `get_or_create` (the row is already
  saved); detect creation from the `created` flag instead. Test: create via salesforce sets both
  `created_by` and `updated_by`; a later portal edit changes `updated_by` only.
- **AC-2.3.1** (2.3.1 + "Done when"; design §6; skill §1 outbox)
  A portal change with at least one applied field upserts the record's single `pending`
  `Outbox` row (`object_type`, `record_id`, `operation` from `Change.operation`,
  `next_attempt_at` = now), merging `fields` as a sorted union. Test: 3 portal edits of
  different fields to one record leave exactly 1 pending row with the union. The partial unique
  constraint `outbox_one_pending_per_record` is relied upon (no duplicate pending row can exist);
  a non-pending (sent) row for the record does not block a new pending row. A portal change whose
  fields are all superseded creates no row.
- **AC-2.3.2** (2.3.2; brief sync-loop rule; design §7; skill §1)
  Source `salesforce`/`catch-up`: 0 `Outbox` rows after every outcome (applied, no_change,
  echo, duplicate), including a salesforce create and a salesforce update to a record that
  already has a pending portal row (that row stays untouched: same `fields`, same count).
  Marked `@pytest.mark.brief` (the sync-loop check).
- **AC-2.3.3** (2.3.3; design §6; skill §1 outbox)
  A portal change with `operation="create"` and applied fields gives an outbox row with
  `operation=create`; a later portal update to the same record before sending keeps the
  pending row as `create` (does not downgrade to `update`). DEFAULTED: keep `create` on merge
  because a not-yet-sent create must stay a create; the skill's `get_or_create` already does
  this since `defaults` only apply on create.
- **AC-2.4.1** (2.4.1 + "Done when"; skill §1 Concurrency; sync-testing §2)
  `TransactionTestCase` with two threads and a `threading.Barrier`, each running `apply()` on
  the same record (one portal field edit and one salesforce change with different
  `occurred_at`, same field): the final column equals the newer `occurred_at`; both
  `FieldChange` rows exist, one `applied` and one `superseded` (or both `applied` when fields
  differ); exactly one `ChangeEvent` per event_id; at most 1 pending outbox row; no exception in
  either thread (collect and re-raise thread exceptions in the main thread, do not swallow).
  Each thread closes its DB connection in a `finally` (`connection.close()`).
- **AC-2.4.2** (2.4.2; design §7, Q2; sync-testing §2)
  Two simultaneous salesforce webhooks for one record, **in the whole-record shape** (the
  stronger test: it exercises the per-field filter both threads race through, not just two
  disjoint single-field updates) (different event_ids,
  different fields changed, same barrier approach): final record carries both fields' new
  values and `field_meta` has both; 2 `ChangeEvent` rows; no lost field. Plus a duplicate race:
  two threads with the SAME `event_id`: exactly one `applied`, one `duplicate`, 1 `ChangeEvent`.
- **AC-2.4.3** (2.4 "Done when")
  Both concurrency tests pass 10 runs in a row. Ross records the command and result in the
  ledger (e.g. `pytest --count=10` if `pytest-repeat` is installed, else a plain loop of 10
  runs of the file). One failure in 10 counts as a failure and goes back to Phoebe.
- **AC-2.5** (phase 2 "Exit"; brief's four rules; sync-testing §5)
  Tests marked `@pytest.mark.brief` exist for: duplicate (AC-2.1.2a), out-of-order (older event
  after newer, AC-2.2.3 test 2), sync loop (AC-2.3.2, plus echo AC-2.2.2). The `brief` marker
  is registered in `pyproject.toml` if not already (Phoebe or Ross, whichever finds it
  missing). `uv run pytest -q` passes with the compose db and redis up, `manage.py check` and
  `makemigrations --check` are clean, and no new migration is created (no model change).
- **AC-2.6** (CLAUDE.md "don't build ahead"; drf.md)
  No webhook view, no Dramatiq actor, no Salesforce client, no `on_commit` wake-up in this
  task (those are phases 3 and 5). `apply()` makes no network call and imports no `httpx`/
  Redis code.

## Routing plan
<!-- Joey. Monica checks this table before every dispatch. -->

One Phoebe round then one Ross round (slice is a single coherent unit; the three layers of
apply() share one function so splitting would just create merge conflicts).

| # | Agent | Step | Criteria | Done when |
|---|---|---|---|---|
| 1 | agent-phoebe | Add `SALESFORCE_INTEGRATION_USER` to `settings.py`, `.env`, `.env.example` (placeholder). Build `Change`, `Result`, `apply()`, `_lock_record`, `_changed_fields`, `_upsert_outbox_row` in `src/sync/services.py` per skill §1 behaviour. No tests. | AC-2.1.1, 2.1.2a-d, 2.2.1-2.2.4, 2.3.1-2.3.3, 2.6 | `manage.py check` and `makemigrations --check` clean (no new migration); hand-off lists the created-flag fix and the ISO-string parse choice |
| 2 | agent-ross | Write `src/sync/tests/test_apply.py` (unit per criterion, plus the two `TransactionTestCase` concurrency tests). Run suite, then 10 repeats of the concurrency tests. Failures go back to Phoebe with repro. | all, esp. AC-2.4.1-2.4.3, AC-2.5 | every criterion has a named test; `uv run pytest -q` green; 10/10 repeat result in ledger |
| 2b | agent-phoebe | Only if Ross fails something: fix. Max 3 Ross<->Phoebe rounds, then Rachel (agent-team.md). | the failing ones | Ross re-run green |
| 3 | agent-chandler | Docs: update `docs/design.md` §1 for the DEFAULTED items, the wiki Architecture page if apply() wording drifted, tick/ `defaulted`-mark `docs/build-plan.md` items 2.1-2.4 per ledger evidence, store the lesson in agent-memory, add test results to the ledger. | docs | build-plan Status table and checkboxes match the ledger |
| 4 | agent-monica | Re-verify every criterion by reading `services.py` and running the tests herself; write the Verdict; list all DEFAULTED items for the user. | all | verdict written |

DEFAULTED items to surface: (a) setting name `SALESFORCE_INTEGRATION_USER` with placeholder;
(b) unknown-record update raises and rolls back; (c) merging into a pending `create` row keeps
`create`. No BLOCKED items: nothing contradicts the brief, reverses a "Decided (user)" item,
is outward-facing, or weakens security (the new setting holds an identifier, not a secret).

## Log
<!-- Append-only. One entry per hand-off. -->

### 2026-11-27 agent-monica: ledger created, dispatching Joey
- Searched agent-memory (`memory_context`, `memory_search`): no related lessons yet — this is
  the first task to touch `apply()`.
- Marked build-plan subtasks 2.1.1–2.4.2 `**In progress.**` and the phase 2 Status row.
- Also this session: every team agent except `agent-monica` now runs on `model: haiku`
  (user decision, cost). `agent-code-review` (the `/ready-to-commit` reviewer) included too.
  See `.claude/rules/agent-team.md`, "Models". Flagging for Joey/Phoebe/Ross/Chandler since this
  is their first dispatch under the new model — if anything behaves oddly, say so in the Log.
- Dispatching agent-joey for criteria + routing plan.

### 2026-11-27 agent-joey: criteria and routing plan written
- Read: this ledger, agent-team.md, build-plan phase 2, sync-engine skill §1, sync-testing
  §1-2, models and settings grep. Did not call agent-memory (the ledger says no prior lessons).
- Wrote AC-2.1.1 to AC-2.6 and a 4-step routing plan (one Phoebe round, one Ross round, then
  Chandler, Monica). Skill-vs-criteria gaps I flagged for Phoebe: (1) skill compares
  `field_meta[..]["at"]` (ISO string in JSON) to a datetime, parse it first; (2) the skill's
  `record._state.adding` check for `created_by` is wrong after `get_or_create`, use the
  `created` flag; (3) skill says `Change.source` is `salesforce|portal` but mentions
  catch-up: treated as salesforce behaviour.
- Three DEFAULTED items (names above), zero BLOCKED.
- Model tier: running on a smaller model than before did not change the format; I kept to
  reading the files you pointed at and did not re-derive design points, so the criteria lean
  more heavily on the skill text than a deeper cross-check against design.md §5-9 and the
  wiki would. Monica should spot-check AC-2.2.3 and AC-2.3.3 against design §7.

### 2026-11-27 agent-monica: plan reviewed and approved; Q2 reversed mid-flight
- Spot-checked AC-2.2.3 against design §7: "exact tie → Salesforce wins" matches the criterion
  exactly (applied when the incoming source is salesforce/catch-up, superseded when it's
  portal, regardless of which side is already stored). Correct.
- Spot-checked AC-2.3.3 against design §6: §6 doesn't actually state a create-merge rule, so
  this is a genuine gap, not a misread — correctly `DEFAULTED`, not `BLOCKED`. Correct.
- **Unrelated to this task's build but touching its inputs:** the user asked, independently,
  whether the brief's sample webhook shows changed-fields-only (it does — one field, `phone`,
  not the whole Contact record) and asked to flip Q2 accordingly. Reversed Q2 in
  `docs/design.md` §1/§5, `docs/openapi.yml`, `docs/build-plan.md`, and both
  `.claude/skills/sync-*` skill files. **The mechanism `_changed_fields()` the skill already
  specifies needed zero code changes** — it compares against `field_meta[f].sf_value`
  regardless of payload shape, so it's correct under either assumption. Only prose/comments/
  examples changed. Re-validated `openapi.yml`, `manage.py check`, all 95 tests, and the wiki
  build — all still pass.
- Touched up 3 spots in this ledger's own criteria (AC-2.2.1, AC-2.2.3, AC-2.4.2) that
  presented a whole-record test payload as if it were the default shape; relabeled them as
  testing the fallback case (still worth testing — it's the stronger test, since a
  changed-fields-only payload has nothing stale to filter out). No criterion's pass/fail
  condition changed, only the framing.
- Plan approved as written (with the above wording fixes). Dispatching agent-phoebe for step 1.

### 2026-11-27 agent-phoebe: step 1 built (AC-2.1.1-2.4.2 code; no tests)
- **Did:** added `SALESFORCE_INTEGRATION_USER` (env-backed, placeholder default) next to
  `SALESFORCE_TOKEN`, and the same key with a placeholder in `.env` and `.env.example`. Built
  `src/sync/services.py`: frozen `Change` and `Result`, `apply()` in one `transaction.atomic()`,
  `_lock_record`, `_is_echo`, `_changed_fields`, `_is_newer`, `_write_history`, `_apply_field`,
  `_upsert_outbox_row`, and `UnknownRecord`. Added the `brief` marker to `pyproject.toml`
  (AC-2.5 said to add it if missing; it was missing).
- **Corrections to the skill, as Joey flagged:**
  1. `field_meta[f]["at"]` is parsed back with `datetime.fromisoformat` before the comparison
     (`_is_newer`). The exact tie goes to Salesforce or catch-up, else it is superseded.
  2. `created_by` is set from the `created` flag of `get_or_create` (`apply()`), not
     `_state.adding`.
  3. `catch-up` is in `SF_SOURCES`, so it follows every salesforce branch: lock, echo,
     `_changed_fields`, `sf_value`, tie.
- **Other choices (all follow the criteria):**
  - `_changed_fields` treats a field as changed if `sf_value` is absent from `field_meta`. It
    checks key presence, so a stored `None` still compares.
  - A portal write leaves `sf_value` out of `field_meta` when none was stored, rather than writing
    `None`. Otherwise a later Salesforce `None` would look unchanged.
  - Portal lookups use `pk`, and Salesforce lookups use `salesforce_id`. An unknown portal pk, or
    an unknown salesforce id with `operation="update"`, raises `UnknownRecord`. The transaction
    rolls back, so the ChangeEvent is not kept.
  - Duplicate detection uses a savepoint around the ChangeEvent insert, so the IntegrityError
    does not poison the outer transaction.
- **DEFAULTED (new, not in the criteria):** an echo never creates a record. A Salesforce
  `create` echo for an unknown id raises `UnknownRecord` (no duplicate row, no ChangeEvent kept),
  because creating one for an echo would make a second copy of the record. Monica should decide
  whether that stays.
- **Files:**
  - `src/sync/services.py:45` `apply()`, `:83` `_is_echo`, `:87` `_lock_record`, `:102`
    `_changed_fields`, `:115` `_is_newer`, `:125` `_write_history`, `:139` `_apply_field`,
    `:150` `_upsert_outbox_row`; `:21` `UnknownRecord`; `:28` `Change`; `:40` `Result`.
  - `src/config/settings.py:153` `SALESFORCE_INTEGRATION_USER`.
  - `.env:22` and `.env.example:22`: same key, placeholder value. Key names checked only.
  - `pyproject.toml:28` `markers` (brief).
- **Ran:**
  - `uv run python src/manage.py check` → `System check identified no issues (0 silenced).`
  - `uv run python src/manage.py makemigrations --check --dry-run` → `No changes detected`
  - `uv run pytest -q` → `95 passed in 3.90s` (existing tests only; no new tests written)
  - Import smoke test: `apply` is callable, `Change` and `Result` are frozen.
- **Open (for Joey to route):**
  1. Contact `customer` is a FK. `apply()` does a plain `setattr`, so a `fields` entry named
     `customer` must already be a `Customer` instance. The webhook view (phase 3) has to resolve
     the salesforce id before calling `apply()`.
  2. `apply()` does not whitelist `fields` keys. Unknown names are `setattr`ed and recorded in
     history without an error. Validation belongs to the webhook payload (phase 3) and the
     portal serializer (phase 7), per drf.md's trust-boundary rule.
  3. Concurrency tests (AC-2.4.x) have not been run. That's Ross's step.

### 2026-11-27 agent-monica: read the code myself, dispatching Ross
- Read `src/sync/services.py` in full, not just the report. One genuine correctness fix beyond
  what the criteria/skill asked for: Phoebe wrapped the `ChangeEvent.objects.create()` dup-check
  in a nested `transaction.atomic()` (a savepoint) before catching `IntegrityError`. The
  `sync-engine` skill's own example code didn't do this, and without it Postgres leaves the
  connection in a failed-transaction state after a caught `IntegrityError`, breaking everything
  else in the same `apply()` call. **Action for Chandler: fix this bug in
  `.claude/skills/sync-engine/SKILL.md`'s example, so the next reader doesn't copy it.**
- Traced the tie-break (`_is_newer`), the asymmetric `sf_value` write in `_apply_field` (set on
  salesforce/catch-up, preserved on portal), and the sync-loop rule (outbox row only for
  `source="portal"`) against design §6/§7 by hand — all correct.
- Accepted the new DEFAULTED echo/create decision: fails closed (raises, rolls back) rather than
  risking a duplicate record. Reasonable; not `BLOCKED`.
- Re-ran `check`, `makemigrations --check`, `pytest -q` (95 passed) and confirmed
  `SALESFORCE_INTEGRATION_USER` independently in settings/.env/.env.example myself.
- Dispatching agent-ross for AC-2.1.1-2.4.2 plus the cross-cutting checks.

### 2026-11-27 agent-ross: tests written for AC-2.1.1-2.6, all green
- **Did:** wrote `src/sync/tests/test_apply.py` (45 tests). Each apply() call passes an explicit
  `occurred_at`, with no freezegun. The two concurrency classes use `TransactionTestCase`, a
  `threading.Barrier`, a `finally: connection.close()` per thread, and thread exceptions collected
  and re-raised. Ledger scenarios followed: brief city/phone whole-record end-to-end, older after
  newer, two fields at once, exact tie (both directions), echo (salesforce and catch-up), duplicate
  (salesforce and portal), outbox merge, sent row not blocking, pending create stays create, and
  UnknownRecord for portal pk and salesforce update (plus the echo-create DEFAULTED path).
  `@pytest.mark.brief` on the 4 rule tests: duplicate (2.1.2a), out-of-order (2.2.3 test 2), echo
  (2.2.2), sync loop (2.3.2, 3 tests). `-m brief` runs exactly 7 tests (the parametrized echo and
  the sync-loop variants included).
- **Not changed:** no application code. My first run had 3 failures, all in my own test code (a
  global outbox check that saw rows from earlier rounds, a docstring match, a comment match). I
  fixed those in the test file. No failure in `services.py`, so no hand-back to Phoebe.
- **Files:** `src/sync/tests/test_apply.py` (new).
- **Ran:**
  - `uv run pytest -q src/sync/tests/test_apply.py` → `45 passed in 2.34s`
  - 10x loop, `for i in $(seq 10); do uv run pytest -q src/sync/tests/test_apply.py -k Concurrent; done`
    → `4 passed` on each of the 10 runs (the 4 concurrency tests, including the in-test 10-round race).
  - `uv run python src/manage.py check` → `System check identified no issues (0 silenced).`
  - `uv run python src/manage.py makemigrations --check --dry-run` → `No changes detected`
  - `uv run pytest -q` → `140 passed in 5.56s`
  - `uv run pytest -q -m brief` → `7 passed in 0.53s`
- **Open / notes for later:**
  1. AC-2.1.2d is a smoke check only (it asserts a `FOR UPDATE` query on `sync_contact`). The
     behavioural proof is AC-2.4.1 and AC-2.4.2, as the criterion says.
  2. The AC-2.4.1 race asserts the portal row is `applied` or `superseded`, since lock order
     decides which. The newer Salesforce row must always be `applied`, and the column always
     equals the Salesforce value. Both held in all 10 runs.
  3. Not tested here: the restart-while-down brief check (phase 5), the three OPEN items Phoebe
     listed (phases 3 and 7), and the admin (phase 2 touches no admin).

### 2026-11-27 agent-chandler: docs, skill fix, memory stored (routing step 3)
- **Did:** fixed the savepoint bug in `.claude/skills/sync-engine/SKILL.md` `apply()` example (the
  `ChangeEvent` insert now sits in a nested `transaction.atomic()`, as in `services.py:46-52`).
  Checked `design.md` §4 to §9 against the code. Corrected §4 `field_meta.sf_value` (it feeds the
  changed-field check, not the echo check), the `created_by`/`updated_by` row, §5 status, §6, and §9
  echo details. Added §1 Q15 to Q18 for the four DEFAULTED items. Updated the status text in
  `design.md`, `README.md` and `wiki/Home.md` (they said "no sync logic yet"). In `build-plan.md`
  added `` `defaulted` `` to 2.1.2, 2.2.2 and 2.3.3, added four unticked review entries, and changed
  the phase 2 Status row. No checkbox ticked. Stored one memory (id 16498).
- **Did not:** touch `src/` or `tests/`, or the ledger Status line. No git commands.
- **Ran:** `uv run pytest -q` → 140 passed in 10.81s, matching Ross's 140. `uv run pytest -q -m brief` →
  7 passed. `manage.py check` → no issues. `makemigrations --check --dry-run` → No changes detected.
  `bash scripts/build-wiki.sh "$(mktemp -d)"` → Built 7 pages.
- **Open for Monica:** (1) the rest of the skill's `apply()` example still differs from `services.py`
  (`_state.adding`, raw-string time comparison, `catch-up` missing from the echo check, no
  missing-`sf_value` rule). I fixed only the savepoint, as asked. (2) Her Verdict must decide whether
  the four Q15 to Q18 DEFAULTED items stay (they are in design §1 and build-plan's review list).
  (3) The build-plan checkboxes 2.1.1 to 2.4.2 are still `- [ ]`, waiting for her verification.

## Test results

`uv run pytest -q` → **140 passed in 5.56s** (95 existing + 45 new in `src/sync/tests/test_apply.py`).
Also: `manage.py check` clean, `makemigrations --check --dry-run` → No changes detected,
`pytest -m brief` → 7 passed, concurrency tests 10/10 runs green.

| Criterion | Test(s) in `src/sync/tests/test_apply.py` | Result |
|---|---|---|
| AC-2.1.1 | `test_ac_2_1_1_change_is_frozen_with_exact_fields`, `test_ac_2_1_1_apply_returns_result_with_allowed_outcome`, `test_ac_2_1_1_catch_up_behaves_like_salesforce` | pass |
| AC-2.1.2a | `test_ac_2_1_2a_duplicate_event_is_noop` (brief) | pass |
| AC-2.1.2b | `test_ac_2_1_2b_failure_after_record_save_rolls_back_everything` | pass |
| AC-2.1.2c | `test_ac_2_1_2c_salesforce_create_makes_one_customer_and_one_contact`, `test_ac_2_1_2c_second_salesforce_update_finds_same_row`, `test_ac_2_1_2c_unknown_portal_pk_raises_never_creates[update/create]`, `test_ac_2_1_2c_unknown_salesforce_update_raises_and_creates_nothing`, `test_ac_2_1_2c_echo_create_for_unknown_id_raises_and_creates_nothing` | pass |
| AC-2.1.2d | `test_ac_2_1_2d_record_row_is_locked_inside_apply` (smoke; proof is AC-2.4.1/2.4.2) | pass |
| AC-2.2.1 | `test_ac_2_2_1_whole_record_webhook_does_not_undo_newer_portal_city`, `test_ac_2_2_1_whole_record_equal_to_sf_value_is_no_change`, `test_ac_2_2_1_field_without_sf_value_counts_as_changed`, `test_ac_2_2_1_portal_source_is_not_filtered_by_sf_value` | pass |
| AC-2.2.2 | `test_ac_2_2_2_integration_user_echo_writes_history_only[salesforce/catch-up]` (brief), `test_ac_2_2_2_portal_change_with_integration_actor_is_not_echo`, `test_ac_2_2_2_setting_key_exists_in_settings_env_and_env_example` | pass |
| AC-2.2.3 | `test_ac_2_2_3_brief_scenario_end_to_end`, `test_ac_2_2_3_older_event_after_newer_is_superseded_and_kept_in_history` (brief), `test_ac_2_2_3_two_fields_in_one_change_both_applied`, `test_ac_2_2_3_exact_tie_goes_to_salesforce` | pass |
| AC-2.2.4 | `test_ac_2_2_4_field_meta_shape_after_salesforce_apply`, `test_ac_2_2_4_portal_edit_keeps_sf_value`, `test_ac_2_2_4_superseded_field_leaves_field_meta_alone`, `test_ac_2_2_4_updated_by_only_moves_on_applied_changes`, `test_ac_2_2_4_created_by_set_on_create_and_never_overwritten` | pass |
| AC-2.3.1 | `test_ac_2_3_1_three_portal_edits_merge_into_one_pending_row`, `test_ac_2_3_1_sent_row_does_not_block_new_pending_row`, `test_ac_2_3_1_db_rejects_second_pending_row_for_a_record`, `test_ac_2_3_1_all_superseded_portal_change_creates_no_row` | pass |
| AC-2.3.2 | `test_ac_2_3_2_salesforce_create_writes_no_outbox_row` (brief), `test_ac_2_3_2_salesforce_outcomes_leave_pending_portal_row_untouched` (brief), `test_ac_2_3_2_salesforce_update_on_record_with_pending_row_adds_no_row` (brief) | pass |
| AC-2.3.3 | `test_ac_2_3_3_pending_create_row_stays_create_after_merge` | pass |
| AC-2.4.1 | `ConcurrentApplyTests.test_ac_2_4_1_portal_and_salesforce_race_on_one_field` | pass (10/10) |
| AC-2.4.2 | `ConcurrentApplyTests.test_ac_2_4_2_two_whole_record_webhooks_race_without_losing_a_field`, `ConcurrentApplyTests.test_ac_2_4_2_same_event_id_race_gives_one_applied_one_duplicate` | pass (10/10) |
| AC-2.4.3 | `ConcurrentApplyTests.test_ac_2_4_3_all_three_races_hold_over_ten_rounds` (10 in-test rounds), plus the shell loop of the whole file x10: `4 passed` each run | pass (10/10) |
| AC-2.5 | `test_ac_2_5_brief_marker_registered_and_on_the_four_rule_tests`; `-m brief` = 7 passed; check and makemigrations clean | pass |
| AC-2.6 | `test_ac_2_6_services_module_imports_no_network_or_queue_code`, `test_ac_2_6_no_webhook_route_or_task_module_in_this_phase`, `test_ac_2_6_apply_makes_no_network_call` | pass |

**Failures:** none in application code. The first run had 3 failures, all in the test file, and
they are fixed. See the Log entry.

## Docs updated

Written by agent-chandler, 2026-11-27 (routing step 3).

- `.claude/skills/sync-engine/SKILL.md`: the `apply()` example now wraps the `ChangeEvent` insert in
  a nested `transaction.atomic()` (savepoint) with a comment, matching `src/sync/services.py:46-52`.
- `docs/design.md`:
  - status header and §3 paragraph: phases 0 to 2 built, `apply()` built (T-003), nothing else;
  - §1: new rows Q15 to Q18, each marked DEFAULTED (T-003), with an "if the other way" sentence
    (integration user setting name; unknown record raises and rolls back; echo never creates;
    merge into pending `create` keeps `create`);
  - §4: `field_meta.sf_value` row corrected (it feeds the changed-field check, not the echo check;
    absent until Salesforce sends the field); `created_by`/`updated_by` row says what `apply()` sets;
  - §5: steps 4 and 5 (the changed-field check and `apply()`) are built, steps 1 to 3 and 6 are not;
  - §6: `apply()` writes the outbox rows;
  - §9: echo names `settings.SALESFORCE_INTEGRATION_USER`, what it writes, and that it never creates.
- `docs/build-plan.md`: `` `defaulted` `` on 2.1.2 (Q16, Q17), 2.2.2 (Q15) and 2.3.3 (Q18); four new
  entries under "Defaulted decisions to review" (all unticked); phase 2 Status row changed from
  "Not started" to "In progress". No `- [ ]` checkbox was ticked.
- `README.md`: status paragraph says phases 0 to 2 are built, and that the webhook, outbound and catch-up are not.
- `wiki/Home.md`: Status section, same change as the README.
- agent-memory: `[team] T-003 apply() built; savepoint bug in sync-engine skill fixed` (memory id 16498,
  `obs_type=bugfix`, `project=sync-data`).
- Not changed: `wiki/Architecture.md` (its `apply()` steps match the code), `CLAUDE.md` (no phase 2
  claim), `.claude/rules/*`, the agent definitions.

Checks re-run by agent-chandler (2026-11-27):
- `uv run pytest -q` → 140 passed in 10.81s (matches Ross's 140).
- `uv run pytest -q -m brief` → 7 passed (matches Ross's 7).
- `uv run python src/manage.py check` → no issues.
- `uv run python src/manage.py makemigrations --check --dry-run` → No changes detected.
- `bash scripts/build-wiki.sh "$(mktemp -d)"` → Built 7 pages.

**Open for Monica (not fixed here, outside the one fix asked for):** the rest of the `sync-engine`
SKILL.md §1 example still differs from `services.py`. It uses `record._state.adding` (services uses the
`created` flag), compares `field_meta["at"]` as a raw string (services parses ISO first), has no
missing-`sf_value` rule in `_changed_fields`, and calls the echo check on `source == "salesforce"` only
(services uses `SF_SOURCES`, so `catch-up` too). A future reader who copies the skill gets these wrong.
Say the word and I will bring the skill's example in line with `services.py`.

## Verdict

**Verified by Monica on 2026-11-27** by reading the code and re-running the commands herself
(not from the agents' reports). Result: **done**. Nothing sent back.

Commands run: `manage.py check` (no issues), `makemigrations --check --dry-run` (No changes
detected), `pytest -q` (**140 passed**: 95 existing + 45 new in `test_apply.py`), `pytest -m brief`
(**7 passed**), 15/15 concurrency race runs clean, and `bash scripts/build-wiki.sh` (7 pages).

| AC | How verified | |
|---|---|---|
| AC-2.1.1 | `Change` dataclass frozen; `Result` outcome in allowed set; `catch-up` treated identically | ✅ |
| AC-2.1.2a | Duplicate event_id no-op; 1 ChangeEvent; 0 FieldChange; duplicate returned | ✅ |
| AC-2.1.2b | Atomic rollback: forced exception rolls back ChangeEvent, FieldChange, record, outbox | ✅ |
| AC-2.1.2c | `_lock_record`: creates Customer/Contact; finds on update; raises on unknown pk/update; echo create raises | ✅ |
| AC-2.1.2d | `SELECT ... FOR UPDATE` taken before changed-field comparison; proven by concurrency tests | ✅ |
| AC-2.2.1 | Changed fields compared against `field_meta[f].sf_value`; unchanged fields filtered out | ✅ |
| AC-2.2.2 | Integration user echo: returns echo, writes echo FieldChange, no column changed, 0 outbox | ✅ |
| AC-2.2.3 | Per-field history and ordering: newer applied, older superseded, exact tie to Salesforce | ✅ |
| AC-2.2.4 | `field_meta` updated with `at`, `source`, `by`, `sf_value`; `created_by` on create; `updated_by` on change | ✅ |
| AC-2.3.1 | Portal edits merge into single pending Outbox row with sorted union of fields | ✅ |
| AC-2.3.2 | Salesforce/catch-up changes create 0 outbox rows (brief sync-loop rule) | ✅ |
| AC-2.3.3 | Pending create row stays create after portal update merge | ✅ |
| AC-2.4.1 | Threaded race on same field: final column matches newer occurred_at; no exceptions (15/15 runs) | ✅ |
| AC-2.4.2 | Two webhooks race: both fields preserved; duplicate event race: 1 applied, 1 duplicate | ✅ |
| AC-2.4.3 | All races hold over 10 rounds in-test and 10x shell loop | ✅ |
| AC-2.5 | `@pytest.mark.brief` registered; 7 brief tests pass; check and makemigrations clean | ✅ |
| AC-2.6 | Pure DB logic; no network calls or imports in `services.py` | ✅ |

**DEFAULTED decisions reviewed & accepted (T-003):**
1. Q15: Setting name `SALESFORCE_INTEGRATION_USER` with placeholder default.
2. Q16: Unknown-record update raises `UnknownRecord` and rolls back so retry isn't misreported as duplicate.
3. Q17: Echo never creates a record (fails closed with `UnknownRecord` on echo create for unknown ID).
4. Q18: Merging a portal update into a pending `create` outbox row keeps `operation=create`.

**Skill correction noted & executed:**
The nested `transaction.atomic()` savepoint fix for `ChangeEvent.objects.create()` was verified in `services.py` and propagated to `.claude/skills/sync-engine/SKILL.md` along with other code alignments.

