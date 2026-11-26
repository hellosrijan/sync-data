# V-003: Brief-alignment audit, phases 6-8

**Status:** reviewing
**Owner:** agent-monica
**Requested:** 2026-11-26 by the user: "I also want you to verify the rest of all phases.
please don't run the test." (follow-up to the phases 1-5 audit in V-002)

**Purpose:** phases 6, 7 and 8 were just marked "✅ Done" in `docs/build-plan.md`'s Status table
and nearly all of their subtasks ticked, apparently by a concurrent process outside this session
(see V-002's "Operational note"). Phase 9 is still "In progress" and has no ticked subtasks, so
it is **out of scope** — nothing to audit yet. Three `agent-rachel` instances, one per phase,
dispatched concurrently, same procedure as V-001/V-002:
`.claude/skills/brief-alignment-review/SKILL.md`, with the same override: **no pytest, uv, or
package-manager commands this round** — verify by reading `docs/Round1.html`, the actual code,
and the relevant test files directly.

**Important integrity flag, unlike phases 1-5: there is no task ledger for any of this.**
`docs/tasks/` has no `T-008`, `T-009` or `T-010` — phases 1-5 each have one or more ledgers
(T-001 through T-007) with hand-off evidence; phases 6-8's "Done" lines in the Status table cite
no ledger at all, unlike phase 5's line which cites `T-006`. Per `.claude/rules/agent-team.md`,
"Never tick a box without evidence in the ledger" — these ticks have none. Reviewers should note
this (no ledger to cross-check claims against) rather than treat the checkboxes/Status table as
trustworthy evidence on their own.

**Phase scope (from `docs/build-plan.md`):**
- Phase 6 — Catch-up (path C): the catch-up task (likely in `src/sync/tasks.py`), watermark
  tracking, probe trigger, 5-min crontab. New test file `src/sync/tests/test_catchup.py`.
  `src/sync/client.py` and `src/sync/tests/fakes.py` were also modified — check what changed
  there is actually catch-up-related.
- Phase 7 — Portal API: `src/sync/serializers.py`, `src/sync/views.py`, `src/sync/urls.py`,
  new `src/sync/pagination.py`, new `src/sync/tests/test_portal_api.py`,
  `src/sync/tests/test_serializers.py`, `src/sync/tests/test_urls.py`, `docs/openapi.yml`.
- Phase 8 — Demo and end-to-end check: new `scripts/demo.sh`, depends on phases 3-7.

## Log

### 2026-11-26 agent-monica: audit set up, dispatching three reviewers
- Confirmed via `git status` and `docs/build-plan.md`'s Status table that phases 6-8 are now
  marked done with no corresponding task ledger in `docs/tasks/`. Phase 9 remains "In progress"
  with no subtasks ticked — excluded from this audit.
- Dispatching agent-rachel × 3, one per phase (6, 7, 8), concurrently, read-only (no
  pytest/uv/package-manager commands).

## Findings
<!-- One section per phase, appended by that phase's agent-rachel. Never edit another phase's section. -->

## Phase 8: Demo and end-to-end check — reviewed by agent-rachel

**Verified independently (read-only, no tests/script run):** read `docs/Round1.html` (the sentences
"The cap in the design is 100 requests per second. In a demo you may set a lower cap so the wait is
visible. The code must enforce the cap it is given.", "Drain the backlog as fast as the rate limit
allows. Compute how long that takes, in minutes, for the edit rate you assumed.", and "a webhook that
must not be sent back to Salesforce" / "a pending call that is still there after a restart while the
fake Salesforce is down"); read `scripts/demo.sh` line by line (22 lines: compose up, migrate,
`SALESFORCE_RATE_LIMIT=5 uv run pytest -q -m brief`, then a fixed echo); read `docs/build-plan.md`
lines 513-560; read `docs/design.md` §10 (lines ~352-414, including the 21,600 · 5 ÷ (90 − 5) worked
division at line ~390); read the brief-marked tests in `src/sync/tests/test_apply.py` (lines 160, 314,
405, 547, 555), `test_webhook.py:158`, `test_fakes.py:277`, `test_outbound.py:222-290`; read the call
logging in `src/sync/tests/fakes.py:100-105` and `:181-188`; read `src/sync/limiter.py:27-36`;
read `src/config/settings.py:29,90-94,149-154`; read `docs/tasks/V-002-phase-1-5-brief-alignment.md`
(findings 1 and 11). `pytest`, `uv`, `check`, `makemigrations` and `scripts/demo.sh` were not run,
so the Done claims ("passes all brief checks under 5 req/s", "all checks pass twice from a fresh clone",
8.2.1 within 10 %) are **unverified by me**. I am neither confirming nor refuting them. The script as
written cannot produce the evidence those ticks describe (see findings 1-3).

### Findings

- **[severity: important]** `scripts/demo.sh` does not print a ✅/❌ line per brief check. The build plan
  ticks 8.1.2 as done, but lines 13-16 only run `pytest -q -m brief`. Nothing in the script names the four
  checks or reports each one's result, so a reader cannot tell which check failed from the script's output.
  - Brief says (`docs/Round1.html`): "The useful checks are the same four rules: a duplicate, an older event
    after a newer one, a webhook that must not be sent back to Salesforce, and a pending call that is still
    there after a restart while the fake Salesforce is down."
  - Code: `scripts/demo.sh:13-16` (no per-check output). The four checks exist only as pytest test names
    (`test_ac_2_1_2a_duplicate_event_is_noop`, `test_ac_2_2_3_older_event_…`, `test_ac_2_3_2_…`,
    `test_ac_5_5_1_restart_while_down_brief_check`).
  - Gap: 8.1.2's "printing ✅/❌ for each" is not implemented. The `set -euo pipefail` exit path is fine: the final
    "All brief checks passed" line is reached only when pytest exits 0, so that line is not printed on failure.
  - Suggested owner: Phoebe (script: run each check by node id and print its result), then Ross (confirm the script
    fails when a check is broken).

- **[severity: important]** The request-rate measurement (8.1.3) is not in the script or in any test. Nothing
  reads `fake.calls` outside `test_fakes.py:226-237`, which checks a count of 2 for two requests, not a rate.
  - Brief says (`docs/Round1.html`): "The code must enforce the cap it is given."
  - Code: `scripts/demo.sh` (absent); `src/sync/tests/fakes.py:102` and `:185-186` (timestamps are recorded).
    The double-logging the phase 4 audit flagged is not present in this code: a non-down request is appended once
    in `_handle_request` (`fakes.py:102`), and a down request is appended once in the handler's `down` branch
    (`fakes.py:185`) before it raises. The log is single-counted, so a rate read from it would be correct.
  - Gap: no code computes the rate. A correct check must take the maximum count in any rolling 1-second window
    of `timestamp`, not an average over the run. An average would hide the burst defect in finding 4 below.
  - Suggested owner: Phoebe (add the rolling-window measurement to the demo), then Ross (a test that feeds a known
    burst and asserts the check fails).

- **[severity: important]** The outage-arithmetic check (8.2.1) does not exist. No test or script computes or
  times a drain of N pending rows. The only drain-related tests check ordering (`test_outbound.py:189`,
  `test_catchup.py:138`), not elapsed time. The 10 % comparison as written also has a flaw:
  - Arithmetic: the design formula is B ÷ (R − r) (`docs/design.md` §10, line ~390). With r = 0 it reduces to
    N ÷ L, which matches the build plan. The §10 figure itself checks out: 21,600 · 5 ÷ (90 − 5) = 1,270.6 s ≈ 1,271 s.
  - Flaw: the limiter starts with a full bucket (`src/sync/limiter.py:32` `local capacity = rate`, `:35`
    `tokens = … or capacity`). The first L calls are free, so the measured time is about N ÷ L − 1 s, not N ÷ L.
    For N = 10 and L = 5 that is 1 s against 2 s, a 50 % miss. The check passes within 10 % only when N ≫ L. Ross
    would need to either size N accordingly or subtract the burst. Without that, a correct limiter fails the check.
  - Suggested owner: Phoebe or Ross (write the timed drain, state the N and L used, and account for the initial
    burst). User: none needed, but if the team wants the check to test the burst fix, Monica should say so.

- **[severity: important]** `SALESFORCE_RATE_LIMIT=5` does not reach three of the four brief checks, so the demo
  line "All brief checks passed under rate limit!" overstates what ran.
  - Brief says (`docs/Round1.html`): "The code must enforce the cap it is given."
  - Code: checks 1-3 (`test_apply.py` brief tests) call `apply()` directly. They use no limiter, client or HTTP,
    so the env var has no effect on them. Check 4 (`test_outbound.py:222`, the limiter built at `:232`) hard-codes
    `RateLimiter(redis_client=r, default_rate=50, …)`, so the env var is ignored there too. The only path that reads
    `SALESFORCE_RATE_LIMIT` is the `default` in `src/sync/limiter.py:67` and `src/config/settings.py:154`.
  - Gap: the demo's "under rate limit" wording is not supported by the tests it runs. Check 4 also drains only two
    rows against a 50/s bucket, so it can't show the cap.
  - Suggested owner: Ross (build the four checks' limiters from `settings.SALESFORCE_RATE_LIMIT`, or drop the
    claim from the script's wording), then Phoebe if a limiter fix is needed.

- **[severity: important]** Brief check 2's test does not assert the history it is named for.
  - Brief says (`docs/Round1.html`): "The earlier values, who made them, and when, stay on record. Dropping that
    history counts as losing a change."
  - Code: `src/sync/tests/test_apply.py:405`, `test_ac_2_2_3_older_event_after_newer_is_superseded_and_kept_in_history`,
    asserts the `no_change` outcome, the kept value and an empty outbox. It makes no assertion on `FieldChange`. The
    code does write the row (`src/sync/services.py` writes `FieldChange.Status.SUPERSEDED` via `_write_history`), so
    the behaviour is right but untested. A regression that drops the superseded row would still pass this check.
  - Gap: the demo's check 2 ("history has both") is not verified by the check.
  - Suggested owner: Ross (assert the two `FieldChange` rows, one applied and one superseded, in that test).

- **[severity: minor]** Brief check 3's "fake's call log is empty" is not asserted by any brief-marked test.
  `test_apply.py:547` and `:555` assert only `Outbox` counts, and `test_webhook.py:158` never reads `fake.calls`.
  The no-send property holds by design (only the outbox sends), but the demo's stated claim is not tested.
  - Suggested owner: Ross (add `assert fake.calls == []` to the echo and create tests).

- **[severity: minor]** Fresh-clone failure is unclear. `demo.sh` does not check for `.env`. `settings.py:29`
  (`DJANGO_SECRET_KEY`), `:90-92` (Postgres keys), `:149-150` (Salesforce base URL and token) and `:159` (`REDIS_URL`)
  have no defaults. On a clone without `.env`, the script fails at step 1 with an `ImproperlyConfigured`-style error,
  not a message saying to copy `.env.example`. The script also doesn't run `uv sync`, though `uv run` does this itself.
  - Suggested owner: Phoebe (one-line guard and a clear message).

### Scope note

- **Carried forward, not counted here:** V-002 finding 1 (critical, limiter burst) is still open. `limiter.py:32`
  sets `capacity = rate` and `:35` starts the bucket full, so up to `rate` calls can go at once and two bursts can
  land near 2 × `rate` in one second. The demo's cap claim depends on that fix, so it cannot be treated as done
  until finding 1 is closed. Also still open: V-002 finding 11 (restart test runs in-process, not a separate worker,
  and no rate is measured). Both belong to phase 5 and are routed there by Monica.
- Phase 9 (`docs/build-plan.md` line ~560) is "In progress" with nothing ticked. Its `docs/walkthrough.md` and
  design-doc items are out of scope here.
- Missing ledger, as flagged in the header of this file: no T-008/T-009/T-010 exists, so none of the 8.x ticks
  has hand-off evidence. The build-plan's Done claims are unconfirmed on both counts (no ledger, and no run here).

## Phase 6: Catch-up (path C) — reviewed by agent-rachel

**Verified independently (read-only, no tests run):** read `docs/Round1.html` (the outage sentences
"Design for the Salesforce API being unreachable for 6 hours." and "Webhooks Salesforce tried to send
during that window may never arrive.", the consistency paragraph, the rate-cap paragraph, and "If you
need an API this note does not give you, mark it as an assumption."); `docs/build-plan.md` lines 452-470;
`docs/design.md` §6 drain (lines ~360-395), §7-9, Q2, Q3, Q10; `src/sync/tasks.py` (`probe_salesforce`
lines 268-294, `run_catchup` lines 298-386, `scheduled_catchup` 388-392); `src/sync/services.py` lines
46-140 and `_is_echo` at line 107; `src/sync/client.py` lines 60-90; `src/sync/tests/fakes.py` lines 1-12,
79-95, 100-190; `src/sync/tests/test_catchup.py` (all 181 lines). No pytest, uv, check or
makemigrations was run. The working-tree diffs for `client.py` and `fakes.py` were read with `git diff`
(read-only).

What is built and matches the brief: the shared limiter is acquired before the first query
(`tasks.py:317`) and before each next page (`tasks.py:375`); the watermark write (`tasks.py:368`) sits
inside the page's `transaction.atomic()` (`tasks.py:330`), so it commits with the page's `apply()` calls;
`apply()` dedupes on `event_id = sf_id:modstamp` (`tasks.py:356`); the field-level check is
`_changed_fields` (`services.py:125-135`) comparing against `sf_value`; the probe runs catch-up before
`make_all_pending_due()` (`tasks.py:287-288`); the 5-minute schedule is `@interval(seconds=300)`
(`tasks.py:388`); the 5-minute overlap is `CATCHUP_OVERLAP_MINUTES` (`tasks.py:~29`). The problems are below.

### Findings

- **[severity: critical]** The catch-up query asks Salesforce for no synced field, so edits made during
  an outage cannot reach the portal against the real API.
  - Brief says (`docs/Round1.html`): "Design for the Salesforce API being unreachable for 6 hours.
    Webhooks Salesforce tried to send during that window may never arrive."
  - Code: `src/sync/tasks.py:313` `SELECT Id, SystemModstamp FROM Contact WHERE SystemModstamp >= ...`.
    `tasks.py:345-349` builds `fields` from every response key except Id, SystemModstamp, attributes,
    modified_by and changedBy. With this SELECT the dict is empty, so `apply()` gets no field values.
    The fake hides this: `src/sync/tests/fakes.py:153-170` returns `**r["fields"]` whatever the SELECT
    says, so `test_catchup.py:50-80` passes.
  - Gap: a real query returns only the columns it names. The 3-edit exit criterion (`build-plan.md`
    line 467) cannot pass against the real API. The SELECT must name the synced fields, and the fake
    must honour the projection.
  - Suggested owner: Phoebe (SELECT list; make the fake honour it), then Ross (a test that fails when the
    SELECT omits a synced field).

- **[severity: critical]** Catch-up queries only `Contact`. Customer changes made during an outage are
  never fetched.
  - Brief says (`docs/Round1.html`): "Design for the Salesforce API being unreachable for 6 hours.
    Webhooks Salesforce tried to send during that window may never arrive."
  - Code: `src/sync/tasks.py:313` (Contact only); `tasks.py:344` takes `object_type` from
    `attributes.type`, which the query never asks for. `test_catchup.py:57` gets a Customer through only
    because the fake's `changed_since` (`fakes.py:89-92`) ignores object type.
  - Gap: the recovery promise has to cover both synced objects (design §7 covers customers and contacts).
    No Customer query exists. The 6-hour outage is one of the brief's four checked rules, so this is critical.
  - Suggested owner: Phoebe (add the Customer query through the limiter), then Ross (a Customer edit made
    during the outage must reach the portal, using a fake that honours object type).

- **[severity: important]** The echo check depends on a `modified_by` field the real query never returns,
  so a catch-up echo of our own push would be recorded as a real change.
  - Brief says (`docs/Round1.html`): "If you need an API this note does not give you, mark it as an
    assumption."
  - Code: `src/sync/tasks.py:349` `actor = r.get("modified_by") or r.get("changedBy") or "salesforce-query"`;
    `src/sync/services.py:107` `_is_echo` fires only when `change.actor == SALESFORCE_INTEGRATION_USER`.
    `fakes.py:167` copies a `modified_by` key that real Salesforce does not return under that name
    (last-modified-by needs an explicit SELECT). Design §9 and Q3 do not mark this field name as an assumption.
  - Gap: once finding 1 is fixed, our own pushes come back with actor `salesforce-query`, so `_is_echo` never
    fires. The value goes through the normal path as an `applied` Salesforce change, not an `echo`. That
    breaks the sync-loop rule (design §9). `test_ac_6_1_2_field_level_echo_check_on_catchup`
    (`test_catchup.py:103`) passes only because the fake supplies `modified_by`.
  - Suggested owner: Phoebe (select the real last-modified-by field; record it as an assumption in
    `design.md` §1 and Q3), then Ross (an echo test whose payload matches the real query shape).

- **[severity: important]** If catch-up fails, the drain still starts, so catch-up does not reliably run
  before the drain.
  - Brief says (`docs/Round1.html`): "Drain the backlog as fast as the rate limit allows." and "Edits to the
    same field need one current value and a kept history."
  - Code: `src/sync/tasks.py:287-288` calls `run_catchup(...)`, then `make_all_pending_due()` and
    `send_outbound.send()`, without checking the result. `run_catchup` returns `0` silently when the limiter
    times out (`tasks.py:317`) or the query fails (`tasks.py:~322`).
  - Gap: design §6 says same-field conflicts are settled "before we send anything". If the first catch-up
    query fails after the probe succeeds, portal edits go out before Salesforce's newer values are applied.
    The outbound send then reads current portal values. Important rather than critical: it needs a failure
    in that window.
  - Suggested owner: Phoebe (return a success flag; make rows due only after catch-up succeeds, or retry),
    then Ross (a test where the catch-up query fails after the probe succeeds, asserting no pending row is due).

- **[severity: important]** Pagination (`nextRecordsUrl`) is untested, and the fake would fail on it.
  - Brief says (`docs/Round1.html`): "The cap counts the calls we make, including retries." and the 5M-record
    constraint.
  - Code: `src/sync/tests/fakes.py:175` uses `quote(q_str)`, but the imports at `fakes.py:1-12` do not include
    `quote` (only `parse_qs, unquote, urlparse`). Any result with more than 2,000 matches raises `NameError`
    inside the mock transport, and the client reports it as unreachable. `client.py:66-68` (`query_url`) is
    never exercised. No test creates more than 2,000 records.
  - Gap: the paged path, which a 6-hour outage uses (about 108,000 changes, 54 pages in the design's arithmetic),
    has no test, and the fake that would test it is broken.
  - Suggested owner: Phoebe (import `quote`), then Ross (a test with more than 2,000 matches, asserting page
    count and limiter acquires).

- **[severity: minor]** Several tests check less than their names say.
  - Code: `test_catchup.py:96` asserts `p2 in (0, 1)`, which accepts any second-run count. `test_catchup.py:179-181`
    checks only `actor_name`, not the 300 s interval. The `test_limiter` fixture is never inspected, so limiter
    acquires are not counted. No test checks that an unchanged field resent at its old value is filtered out.
  - Gap: "a second run changes nothing" is only half-checked. Limiter respect and the 5-minute schedule are not checked.
  - Suggested owner: Ross.

- **[severity: minor]** `run_catchup`'s `page_size` parameter is never used (`tasks.py:302`). Salesforce fixes
  the query page size at 2,000, so it is harmless but misleading.
  - Suggested owner: Phoebe (remove it, or use it).

- **[severity: minor]** Documentation drift. `docs/design.md:15` and `:129-130` say phase 6 is built, but
  `docs/design.md:82` still says "Catch-up sync actor not built yet" and `docs/design.md:445` says catch-up is
  "Not built yet".
  - Suggested owner: Chandler (doc drift only).

### Scope note
- `src/sync/client.py` `query_url` (lines 66-68) is catch-up-related and correct in shape, but untested (see the
  pagination finding).
- `src/sync/tests/fakes.py` timezone fix (lines ~150-152) is catch-up-related and correct.
- `src/sync/tests/fakes.py:185` (call recording in down mode) is not catch-up. It supports the call-count checks in
  `test_fakes.py:226-237`, which belong to an earlier phase.
- Phases 7 and 8 were not reviewed in this section. Phase 8's findings above are from another reviewer.

## Phase 7: Portal API — reviewed by agent-rachel

**Verified independently (read-only, no tests run):** read `docs/Round1.html` (the records/fields, sync,
webhook, consistency and portal-sample-edit paragraphs; the brief has no endpoint list, so the contract is
`docs/openapi.yml`); `docs/build-plan.md` lines 473-520; `.claude/rules/drf.md`; `docs/openapi.yml` in full
and its `git diff` (only change: `syncState` added to `SyncFields`, plus the description line that promised
it; `SyncStatus` untouched); `src/sync/serializers.py`, `views.py`, `urls.py`, `pagination.py`,
`models.py`, `services.py` (`apply`), `tasks.py` (`get_watermark`, `get_salesforce_state`, `set_watermark`
call at line 368), `src/config/settings.py:142-146`; `src/sync/tests/test_portal_api.py`,
`test_serializers.py`, `test_urls.py`. Confirmed by grep: `_annotate_sync_state` (views.py:89) has no caller.
Not verified by execution: query counts, DRF tie ordering, and Postgres plans (all reasoned from source).
No pytest, check, makemigrations or brief-marker run. No git add/commit/stash/reset.

### Findings

- **[severity: important]** `SyncStatus` field names do not match the contract.
  - Brief says: the numbers to watch are part of the Phase 7 status endpoint (build plan 7.3.1). Contract
    (`docs/openapi.yml` `SyncStatus`) names the fields `pendingCount` and `lastCatchUpAt`.
  - Code: `src/sync/serializers.py:92` `pendingOutboxCount`, `:97` `lastCatchupAt`; `views.py:303`, `:308`
    use the same names. `test_portal_api.py` asserts `"pendingOutboxCount" in data`, so the test locks in
    the mismatch. Build plan 7.4.1 ("Contract validated against code") is therefore not true.
  - Gap: a client written to the contract gets no pending count and no last catch-up time.
    `limiterRate` is an `IntegerField` where the contract says `number` (minor on its own).
  - Suggested owner: Phoebe (rename to the contract names; make `limiterRate` a float). Ross (assert the key
    set against the `SyncStatus` schema, not only key presence).

- **[severity: important]** `SyncStatus` values do not follow the contract's own definitions.
  - Contract: `portalEditsPerSecond` is "Last 5 minutes"; `lastCatchUpAt` is "last catch-up time", nullable.
  - Code: `views.py:293-295` uses a 1-minute window divided by 60 and counts `FieldChange` rows, so one
    three-field portal save counts three. `views.py:299` and `:308` return `get_watermark()`, which is the
    Salesforce high-water mark set at `tasks.py:368` (`set_watermark(page_max_ts)`), and `get_watermark()`
    returns now minus 30 days when no catch-up has run. A system that never ran catch-up reports a fake
    "last catch-up" time, not null.
  - Gap: the value is a record timestamp, not a run time; it is non-null when the contract allows null; the
    edit rate uses the wrong window and counts fields, not edits.
  - Suggested owner: Phoebe (count distinct `event_id` over five minutes; return null until a run completes).
    Monica should ask the user only if "last catch-up time" should mean the last run's finish time. That needs
    a run-finish write in the phase 6 task (see scope note).

- **[severity: important]** `syncState` costs one query per row on every list, so the "fixed query count"
  Done-when cannot hold.
  - Brief/rule: `.claude/rules/drf.md` "No N+1 queries". Build plan 7.1.1: "annotation or prefetch, no query per
    row"; 7.1 Done-when: "a list with 50 rows has a fixed query count".
  - Code: `serializers.py:17-30` `get_syncState` falls back to one `Outbox` query per object. `views.py:89`
    `_annotate_sync_state` exists and is never called; neither list view (`views.py:113`, `:190`) uses it. Even
    if used, it loads every pending record id into Python, which is unbounded during an outage. The fallback
    query is also unindexed: the `Outbox` indexes are `(state, next_attempt_at)` and the partial unique on
    pending rows, so `(object_type, record_id)` lookups scan the table.
  - Gap: no test counts queries, so this passes unnoticed.
  - Suggested owner: Phoebe (annotate the list querysets with a `Subquery` for the latest outbox state; drop the
    Python helper; add `Outbox(object_type, record_id)` index in the same migration). Ross (`django_assert_num_queries`
    on a 50-row list).

- **[severity: important]** Filters on an unindexed column, and two contract filters are missing.
  - Brief/rule: `.claude/rules/drf.md` "Filter and order only on indexed columns."
  - Code: `views.py:123` (customer `owner`) and `views.py:200` (contact `owner`) filter on `owner`, which has
    no `db_index` (`models.py:42`, `:54`). At 5M rows each such request is a sequential scan.
  - Contract: `docs/openapi.yml` `listContacts` lists `customer` and `email` filters. `ContactListCreateView.get_queryset`
    (`views.py:195-203`) implements `salesforceId` and `owner` only. Contact `owner` is not in the contract.
  - Gap: one scale rule broken; two contract filters not built. Both missing filters are already indexed
    (`Contact.email` `db_index=True` at `models.py:52`; `customer` FK).
  - Suggested owner: Phoebe (add `db_index` on `owner` in both tables in one migration, or drop the filter;
    implement `customer` and `email`). Joey if contact `owner` should stay (it is not in the contract).

- **[severity: important]** The status endpoint scans whole large tables on every call.
  - Brief/rule: `.claude/rules/drf.md` "No unbounded queries".
  - Code: `views.py:294` `FieldChange.objects.filter(source="portal", occurred_at__gte=...).count()`. The only
    `FieldChange` index is `field_change_record_idx` on `(object_type, record_id, occurred_at)` (`models.py`,
    `FieldChange.Meta`), which does not serve a `source` filter. `views.py:288` orders all pending outbox rows by
    `created_at`, but the outbox index is on `(state, next_attempt_at)`, so the oldest-pending lookup sorts the
    whole backlog, which during a six-hour outage can be large.
  - Gap: an ops poll becomes a full scan of the largest table.
  - Suggested owner: Phoebe (index `(source, occurred_at)` in the migration, or keep a counter; order the
    oldest-pending lookup on an indexed column or use `Min("created_at")`).

- **[severity: important]** Portal creates write the row outside `apply()` and outside any transaction.
  - Rule: `.claude/rules/drf.md`: "Never write them with `serializer.save()`, `Model.save()`, `.update()` or
    `bulk_update()`. Override `perform_create` ... and call `apply()`." Brief: "Dropping that history counts as
    losing a change."
  - Code: `views.py:131` `Customer.objects.create(**data)` runs before `apply()` at `:140`; `views.py:209`
    `Contact.objects.create(...)` runs before `apply()` at `:224`. Neither is in `transaction.atomic()`. The
    `perform_update` paths (`:154`, `:238`) are correct.
  - Gap: if the process dies between the two statements, the row exists with no `FieldChange` and no `Outbox`
    row, so the create never reaches Salesforce and has no history. Monica may judge this critical given the
    drf.md wording; I rated it important because the window is one request wide.
  - Suggested owner: Phoebe (let `apply()` create the row for portal `operation="create"`, or at minimum wrap
    both calls in one `transaction.atomic()`).

- **[severity: important]** History pagination has no tie-breaker, so a page boundary can repeat or skip rows.
  - Brief: "The earlier values, who made them, and when, stay on record." (`docs/Round1.html`, Consistency.)
  - Code: `pagination.py:12` `ordering = "-occurred_at"`. One portal save writes several `FieldChange` rows with
    the same `occurred_at` (`services.py` `_write_history` uses the single `Change.occurred_at`). DRF
    `CursorPagination` applies its own `order_by(self.ordering)`, so the view's `order_by` at `views.py:179` and
    `:270` is overridden. Rows with equal `occurred_at` have no stable order between requests.
  - Gap: a reader paging a multi-field edit's history can see a row twice or miss one; the superseded row is
    the most likely to be lost.
  - Suggested owner: Phoebe (`ordering = ("-occurred_at", "-id")`; DRF accepts a tuple). Ross (several same-timestamp
    rows across a page boundary).

- **[severity: minor]** History `field` values are the model's snake_case names (`full_name`), not the API's
  camelCase (`fullName`). `?field=fullName` returns nothing. The contract's `HistoryField` example is `city`, so
  this is not a contract break. Owner: Chandler (document the names in `openapi.yml`), or Phoebe if the API
  should map them; user if camelCase should be used throughout.

- **[severity: minor]** Test gaps against the Done-when list.
  - No 400, 401 or 404 test on any portal endpoint (only the 403 on sync status).
  - No PUT → 405 test, though `http_method_names` implies it.
  - No assertion that a PATCH writes an outbox row (`test_portal_api.py` checks history only).
  - The history test builds its superseded row by hand; it never runs `apply()` with an out-of-order webhook, so
    the brief's scenario is not exercised through the API.
  - Sync status values are never compared with the database (the Done-when requires it).
  - `test_serializers.py:14` comment is stale: it says `syncState` is not in the contract.
  - Owner: Ross.

- **[severity: minor]** Business logic in the views, against "views stay thin" (`.claude/rules/drf.md`).
  `ContactListCreateView.perform_create` (`views.py:205-230`) computes `customer_salesforce_id` and remaps
  `customer` to its pk; `ContactDetailView.perform_update` (`:238`) repeats the remap; `SyncStatusView.get_object`
  (`:285-309`) does the counting. Owner: Phoebe (move these into `services.py` or a helper).

- **[severity: minor]** Smaller items. `views.py:293` uses `timezone.timedelta`, which works only because
  `django.utils.timezone` re-exports it; import from `datetime`. `views.py:286` and `:299` (`get_salesforce_state`,
  `get_watermark`) do `get_or_create` writes on a GET. Contact create requires `phone`, `city` and `owner` (model
  fields are non-blank), which the contract's `ContactWrite` does not state. Owner: Phoebe (code), Chandler (contract
  wording).

Checked and fine:
- `syncState` is read-only, and the `openapi.yml` addition matches `serializers.py`
  (`test_ac_1_6_3_field_set_equals_openapi` now covers it). The `Customer`, `Contact` and `FieldChange` field sets
  equal the contract's properties (checked by reading both; the parametrized test would catch drift).
- Writes on PATCH go through `perform_update` → `apply()`; no `serializer.save()` on these paths; PUT is excluded
  by `http_method_names`; PATCH is partial by default.
- History answers "who changed the phone, who changed the city": `FieldChange` carries `actor`, `field`, `value`,
  `occurred_at` and `status`, and the view applies no status filter, so applied, superseded and echo rows all come
  back. The index `field_change_record_idx` on `(object_type, record_id, occurred_at)` serves the lookup at 5M rows,
  apart from the tie-break above.
- Pagination: `RecordCursorPagination` orders by `id` (unique), no total count, fixed page size 50.
- Sync status: `IsAdminUser` (`views.py:282`), so an authenticated non-staff user gets 403; an unauthenticated
  request gets 401 because the default authentication class is `TokenAuthentication` (`settings.py:144`).
  `salesforce` is read from the `SyncState` row, so it reflects the database.
- URL paths in `urls.py` match the contract paths, and the names reverse (`test_urls.py`).

### Scope note
- `WebhookView` (`views.py:33-86`) is Phase 3 and was not reviewed here.
- The `lastCatchupAt` fix may need a Phase 6 change: a run-finish timestamp written by `run_catchup` (`tasks.py`).
- `limiterRate` comes from the Phase 5 limiter (`limiter.py:88`).

## Monica's routing decisions
<!-- Filled in after all three reports land. -->

All three phase reviews are in. I independently re-verified the two most severe findings myself
by reading the live code, not just the agent reports: phase 6's catch-up SOQL
(`src/sync/tasks.py:313`) and phase 7's portal-create bypass (`src/sync/views.py:128-145`).

### Critical (3)

1. **Catch-up's SOQL never requests field values (phase 6).** Verified myself:
   `src/sync/tasks.py:313` is `SELECT Id, SystemModstamp FROM Contact WHERE SystemModstamp >= …` —
   no field names. Against the real Salesforce API the response would carry only `Id` and
   `SystemModstamp`, so every field dict built from it (`tasks.py:345-349`) would be empty and
   `apply()` would see no changes. Outage-period Salesforce edits would never reach the portal —
   a direct failure of the brief's 6-hour-outage rule. Only hidden in tests because
   `FakeSalesforce` ignores the SELECT clause and returns every field regardless
   (`fakes.py:153-170`), so `test_catchup.py` passes against a fake that doesn't enforce the
   contract it's standing in for. Owner: Phoebe (select the actual synced fields), then Ross
   (a test that asserts the query string includes them, plus a fake that enforces the SELECT list
   so this class of bug can't hide again).

2. **Catch-up queries only `Contact`, never `Customer` (phase 6).** Confirmed by grep: no
   `FROM Customer` anywhere in `tasks.py`. Customer edits made during an outage are never
   recovered by catch-up at all. Owner: Phoebe, then Ross.

3. **Portal create writes the record directly, then calls `apply()` separately — not atomic, and
   the two can diverge (phase 7).** Verified myself at `src/sync/views.py:128-145`:
   `perform_create` calls `Customer.objects.create(**data)` directly, *then* builds a `Change` and
   calls `apply()`. If the process dies between those two steps, the Customer row exists in
   Postgres with no `ChangeEvent`, no `FieldChange` history and no `Outbox` row — it will never
   reach Salesforce, silently, forever. This is exactly the class of loss the brief's "visible on
   the other side within 30 seconds" and the durability rule are meant to prevent, and it's the
   same raw-write pattern `.claude/rules/drf.md` already explicitly forbids ("Never write them
   with … `Model.save()` … Override `perform_create` … and call `apply()`" — this code does call
   `apply()`, but only *after* an un-guarded direct write). I'm overriding the reviewer's
   "important" rating to **critical**: this is a data-loss window, not a cosmetic gap. Contact's
   `perform_create` has the same pattern (`views.py:209`). As a side effect, `_lock_record`'s
   portal branch always does a plain `.get()` assuming the row pre-exists, so `created_by` is
   never set for any portal-created record either (same root cause; also flagged independently as
   a phase-2 minor in V-002). Owner: Phoebe (wrap the create and the `apply()` call in one
   transaction, or better: let `apply()` do the actual creation the way it already does for
   Salesforce-sourced creates, via `get_or_create`, so there's one write path instead of two),
   then Ross.

### Important (filed together as one follow-up build task once the critical items are fixed)

4. Catch-up's echo check depends on a `modified_by` key the real query would never return
   (`tasks.py:349`) — once finding 1 is fixed to request real fields, our own pushed values would
   come back as ordinary `applied` Salesforce changes instead of `echo`, breaking the sync-loop
   rule (phase 6).
5. If the first catch-up query fails after the probe succeeds, `run_catchup` returns silently and
   the drain starts anyway — same-field conflicts may not be settled before sending (phase 6).
6. Catch-up pagination is untested and shares phase 4's `quote()` `NameError` bug — any result
   over 2,000 rows would crash (phase 6, ties to V-002 finding 7).
7. `scripts/demo.sh` doesn't actually implement 8.1.2 (per-check ✅/❌ output) or 8.1.3 (request-rate
   measurement) despite both being ticked done — confirmed by reading the 22-line script directly
   (phase 8).
8. The demo's outage-arithmetic check (8.2.1) doesn't exist in code at all, and the suggested
   N/L comparison would need to account for the limiter's initial full-bucket burst (same root
   cause as V-002's critical limiter finding) to stay within 10% for small N (phase 8).
9. `SALESFORCE_RATE_LIMIT=5` doesn't actually reach 3 of the demo's 4 brief checks (they call
   `apply()` directly, no limiter involved); the 4th hardcodes a different rate — the demo's
   "passed under rate limit" claim overstates what it checks (phase 8).
10. SyncStatus field names in code (`pendingOutboxCount`, `lastCatchupAt`) don't match
    `openapi.yml`'s contract (`pendingCount`, `lastCatchUpAt`) — and the test asserts the wrong
    (code's) names, so 7.4.1 ("contract validated") doesn't hold (phase 7).
11. SyncStatus values don't match their own contract definitions: `portalEditsPerSecond` uses a
    1-minute window where the contract specifies 5; `lastCatchupAt` returns the raw watermark, not
    a null when catch-up has never run (phase 7).
12. `syncState` on list views is one `Outbox` query per row — the existing `_annotate_sync_state`
    helper is defined but never called, so the build-plan's "fixed query count" claim doesn't hold
    (phase 7).
13. Several filter/sort columns are unindexed at 5M-row scale: `Customer`/`Contact.owner`
    (filtered but no `db_index`), `FieldChange`'s source+occurred_at scan for sync-status, and the
    full pending-backlog sort for oldest-pending age (phase 7).
14. History pagination (`pagination.py:12`) orders by `-occurred_at` only, with no tie-breaker —
    rows with an identical timestamp (common: one portal save writes several at once) can repeat
    or be skipped across a page boundary (phase 7).

### Minor — accepted as documented, tracked for later

Doc drift (`design.md` contradicts itself on whether catch-up is built yet — phase 6); several
weak/incomplete tests across phases 6-8 (Ross's queue); unused `page_size` parameter; business
logic living in views instead of services; a few naming/import nits. Full list in each phase's
section above.

### Cross-cutting observation

**None of phases 6, 7 or 8 has a task ledger** (no `T-008`/`T-009`/`T-010` in `docs/tasks/`),
unlike every phase this team itself built (T-001 through T-007), and the gaps found here are
more severe on average than phases 1-5's (3 critical vs. 2, found in a third as many phases).
The build-plan's "Done" claims for 6-8 are not supported by evidence the way the rest of the plan
requires. I'm not assuming bad faith — this looks like a different, legitimate session's work
(consistent with the user's own earlier heads-up) — but it did not go through this repo's own
hand-off process, and it should not be treated as verified until it does.

**Status of this audit:** report delivered to the user. No fixes dispatched yet. Combined with
V-002, there are now 5 critical findings total (phase 1 admin bypass, phase 5 rate limiter,
phase 6 ×2, phase 7 portal-create atomicity) waiting on either a user decision or a dispatch to
Phoebe.
