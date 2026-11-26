# Design: two-way sync of customer and contact records

Source of truth: [`Round1.html`](Round1.html). Section order follows the brief's outline.
Status: **design in progress**. Phase 0 is built (Compose stack, Django project). Phase 1 is built:
tasks 1.2 to 1.7 (ledgers T-001 and T-002). That covers the six models and their constraints,
the audit columns `created_by` and `updated_by`, token authentication, admin, the `api/v1/` URL layout,
serializers and `GET /api/v1/health/`. Phase 2 is built (ledger T-003): `apply()` (`src/sync/services.py`),
the rules core, with no network. Phase 3 is built (ledger T-004): `WebhookView` at
`/api/v1/webhooks/salesforce/`, `WebhookEventSerializer`, customer linkage, and dedupe purge task (160 tests passing, 8 marked `brief`).
The outbound worker and catch-up are not built yet.
Decided: §5 to §9; Q4 to Q9 and Q11 in §1;
all three metrics in §11. Q1 to Q3, Q10, Q12 and Q13 are recorded as assumptions, and §10 is
arithmetic on them. Q14 (portal login) is a default assumption from the build plan. Q15 to Q18
(echo setting, unknown record error/rollback, echo never creates, pending create merge) are default
assumptions from phase 2 (T-003). Q19 and Q20 (Contact customer linkage via `customer_salesforce_id`
and sanitizing `None` to `""` on string fields) are default assumptions from phase 3 (T-004). §3 and §4 are
set by the build plan ([`build-plan.md`](build-plan.md)) and the API contract
([`openapi.yml`](openapi.yml)). Still TODO: §12 "anything else we cut".

Targets every section must close against: 5 million records; ≤ 30 s sync while both sides are
up; ≤ 100 requests/sec to the Salesforce record API, retries included; a 6-hour Salesforce
outage with no lost work.

Terms, used the same way in every section:

- **Webhook**: an HTTPS request from Salesforce to our endpoint. Carries the brief's sample fields:
  `eventId`, `recordId`, `objectType`, `occurredAt`, `changedBy`, `changedFields`.
- **Outbound call**: an HTTPS request from our worker to the Salesforce record API (create or update).
- **Pending call**: an outbound call that is not yet sent. It is one row in the Postgres `outbox`
  (§9, §10), not a Redis message.
- **Portal edit**: a save in the portal by a finance or customer-success user.

## 1. Assumptions and open questions

Each answer needs: the assumption we take, and one sentence on what changes if it goes the
other way.

| # | Question (from the brief) | Assumption | If the answer is the other way |
|---|---|---|---|
| Q1 | Peak edits/sec, and edits/sec in a quiet hour? | **Assumption: peak 10 edits/s** (5 Salesforce, 5 portal); working-hours average 3.5/s; **quiet hour 0.2/s**. Two independent estimates agree: 5M records × 2 % touched per working day ÷ 28,800 s × 3 (peak factor) ≈ 10/s; and about 500 users across both systems (sales and portal) × 1 save per minute at peak ≈ 8/s in total. The 50/50 split is deliberately pessimistic, because only portal edits fill the outbox. | Drain time scales as 360 · r ÷ (90 − r) min (§10). Double the portal rate (10/s) gives 45 min. At 45/s draining takes as long as the outage. At 90/s or more the backlog never drains, and we'd need Salesforce's batch API (another assumption). |
| Q2 | Does a webhook carry only the changed fields, or the full record? What does the rule do in the other case? | **Assumption (user, revised 2026-11-27): the webhook carries only the changed fields.** The brief's own sample (`changedFields: {"phone": "..."}`) shows exactly one field, not the whole Contact record, so this now matches the sample rather than the earlier, unevidenced "safer" guess. The rule doesn't actually depend on which is true: per field, a value counts as changed only if it differs from **the last value we synced from Salesforce** for that field (`field_meta[f].sf_value`), which we store per field. That makes the mechanism correct either way — see "if the other way". Catch-up results are handled the same way. | **If a webhook sometimes carries the whole record instead** (this was the original assumption, now reversed): nothing breaks, because the `sf_value` comparison is applied the same way regardless of how many fields are present. An unchanged field resent at its old value is filtered out before it can overwrite a newer portal edit or write a spurious history row. The only cost of being wrong is informational, not correctness: more no-op comparisons than expected. |
| Q3 | Can we ask Salesforce for records changed during the 6-hour outage, or are missed webhooks gone? | **Assumption only, not decided:** Salesforce has a query for records changed since time T, returning about 2,000 records per call. §10 relies on it for catch-up. | If there is no such query, changes made in Salesforce during the outage are lost unless a webhook already arrived, and path C cannot recover them. |
| Q4 | Two portal users edit the same field: what does the other user see, and what do we send to Salesforce? | **Decided (user): both saves apply, last save wins.** No conflict check. The save with the later server time is current, and both are written to history with who and when (§7). The other user sees the newer value the next time they load the record, with no warning. Salesforce gets the **current** value (§6). If both saves are pending when the worker runs, that's one call with the final value. A portal save also overwrites a Salesforce change that arrived after the form was opened, because the save is newer. | If users had to be protected from silently overwriting each other, each field would carry the version the user loaded, and a save on a field changed since then would be refused with 409 Conflict, showing the current value and who changed it. |
| Q5 | A portal edit has no time field: what do we trust for order, and where do the time and the actor come from? | **Decided:** time = our server clock when the save arrives; actor = the logged-in portal user. See §7. | If the portal sent its own time, a wrong client clock could reorder edits; we'd have to trust or correct it. |

Questions added while writing this doc (not in the brief):

| # | Question | Assumption | If the answer is the other way |
|---|---|---|---|
| Q6 | Which process hosts the webhook endpoint, and what crosses from it to the sync worker? The config has two places today: Django web (on the host) and the worker container (Dramatiq and the cron scheduler, two processes). | **Decided (user): two services.** The **Django web process** hosts the webhook endpoint and the portal API. The **Dramatiq worker** sends to Salesforce (path B) and runs catch-up (path C). They never call each other. What crosses the boundary: outbox rows in Postgres (the work), plus a nudge through Redis that there's work. The worker writes results (`sent`, the Salesforce ID, catch-up changes through `apply()`) back to Postgres. | With one service, retries piling up during a 6-hour outage would run in the same process that has to answer webhooks and portal users quickly. A crash or deploy would also stop both. |
| Q7 | Outbound retry policy during an outage. The worker uses Dramatiq's default backoff (see §6), so a pending call can wait hours after Salesforce is back. The brief says drain as fast as the cap allows. Which policy? | **Decided: the outbox drives retries, not Dramatiq** (§6). Unreachable, timeout or 5xx: retry at 1, 2, 4, 8 s…, capped at 60 s. 429: pause the shared limiter until `Retry-After`, halve the rate, then add 5 req/s every 30 s back to 90. 400-type rejection: `needs_review`. 401/403: stop all sending and page. After 5 consecutive unreachable failures Salesforce is marked down: sending stops and a probe runs every 10 s. On recovery, catch-up runs, then every pending row is due at once. | With Dramatiq's default backoff, a retry can wait hours or days, and after 20 retries the call goes to Dramatiq's dead-letter set. The §10 drain figures would not hold, and Q9 would be broken. |
| Q8 | Redis AOF is on, but the fsync setting is Redis's default (`everysec`). A host crash can lose about 1 s of writes. Is that acceptable for pending calls, or do we set `appendfsync always`? | **Closed: no longer matters.** Redis holds no pending calls. Its messages only wake the worker, and a timer task every 5 s backs up a lost wake-up. The pending call itself is an outbox row in Postgres. | If pending calls lived in Redis, `everysec` could lose about 1 s of them on a crash, and we would need `appendfsync always` or the outbox anyway. |
| Q9 | After 20 retries (21 failed attempts in all), Dramatiq marks a pending call failed and stops retrying. It moves to Redis's dead-letter set, which expires it after 7 days (`dead_message_ttl`, library default). Do we drop it, or keep it for a person to look at? | **Decided (user): never dropped.** A call that Salesforce rejects with bad data moves to `needs_review`, raises an alert, and waits for a person to fix and requeue it. Transient failures are retried indefinitely (Q7). Dramatiq's dead-letter set is never used for outbound calls. | If dropping were acceptable, Dramatiq's default would do. But the brief's "keep the work" would not hold, and a lost edit would count as a lost change. |
| Q10 | How far under the 100 req/s cap do we run (the brief says we choose), and how much headroom do we keep for retries? | **Assumption: run rate R = 90 req/s.** 10 req/s is kept for retries, recovery probes and catch-up queries. All of them go through one shared limiter, so the total never exceeds 100. | Running at R = 100 leaves nothing for retries, so a burst of failures would push us over the cap. Running lower (e.g. 50) roughly doubles every drain time. |
| Q11 | How do we check that a webhook really came from Salesforce? The brief does not say. | **Decided (user): DRF `TokenAuthentication`.** A dedicated integration user owns the token, and only the webhook endpoint accepts it. *Assumption:* Salesforce can send a static `Authorization: Token <key>` header with each webhook, over HTTPS. A replayed old webhook can't change data: within 7 days it's a duplicate (§8), and after that it's older than the stored field time, so it's superseded (§7). | If Salesforce can't send a custom header, we'd need another check the brief doesn't give, such as a signed payload or an IP allow-list. |
| Q12 | Are there bulk or mass updates (e.g. reassigning the owner on 50,000 contacts at once)? The brief doesn't mention any. | **Assumption: no.** Edits are interactive, one record per save, at the rates in Q1. | A portal-side bulk update of 50,000 records takes ≈ 9 min at 90 req/s (50,000 ÷ 90 ≈ 556 s), and an interactive edit queued behind it would miss the 30 s target. We'd add two outbox priorities (interactive first), or use Salesforce's batch API (200 records per call: 50,000 ÷ 200 = 250 calls ÷ 90 req/s ≈ 3 s, another assumption). Salesforce-side bulk changes cost nothing, because webhooks aren't capped. |
| Q13 | A create call times out: did Salesforce create the record? Retrying could create a duplicate. | **Assumption: Salesforce supports upsert by external ID.** Every create carries our portal record ID as an external ID, so a repeat matches the existing record instead of making a new one. This is an API the brief doesn't give (it names only create and update). | Without upsert, a create with an unknown outcome can't be retried safely. It would go to `needs_review` for a person to check, and duplicates would be possible after a crash. |
| Q14 | How do portal users log in to the portal API? The brief does not say. | **Assumption (default, build plan): DRF `TokenAuthentication`, one token per portal user**, the same mechanism as the webhook (Q11). There's no UI this weekend, so no sessions or SSO. The actor on a portal edit is the token's user (Q5). | With session login or SSO, only the authentication class on the portal views changes. `apply()`, history and the outbox don't depend on how the user logged in. |
| Q15 | DEFAULTED (T-003): what setting names the Salesforce integration user that echoes are matched against? The brief does not name it. | **Assumption: `SALESFORCE_INTEGRATION_USER`** in `src/config/settings.py`, read from the environment. `.env` and `.env.example` carry a placeholder value (the same key in both). The value is an identifier, not a secret. | If the brief or Salesforce names the integration user differently, rename the setting and the value. Echo matching is one comparison, so nothing else changes. |
| Q16 | DEFAULTED (T-003): what happens when a portal change names a record that does not exist, or a Salesforce update names an unknown `salesforce_id`? | **Assumption: raise `UnknownRecord` and roll back.** Nothing is created, and the `ChangeEvent` is not kept, so a retry is not reported as a duplicate. Design is silent on an update to an unknown record, and swallowing it would lose data. | If the answer were "create it", a Salesforce update for an unknown id would create a row with partial data. The rollback rule would also change, so a retry would need its own dedupe rule. |
| Q17 | DEFAULTED (T-003): can an echo create a record? A Salesforce `create` echo (integration user) for an id we do not hold. | **Assumption: no.** An echo never creates a record, so a second copy of a record we already sent never appears. The echo raises `UnknownRecord` as in Q16. | If an echo should create, the record would exist without a real Salesforce change behind it. Allowing it means the echo rule has to write the record, not only history. |
| Q18 | DEFAULTED (T-003): a portal change merges into a pending outbox row whose operation is `create`. Does the merged row stay `create`? | **Assumption: yes, it stays `create`.** A create that has not been sent yet must still be sent as a create. `operation` is set only when the row is inserted, and a merge changes `fields` alone. | If the merged row became `update`, Salesforce would receive an update for a record it does not have yet. The merge rule would need to check the operation of the earlier row. |
| Q19 | DEFAULTED (T-004): a Contact webhook arrives with a `customer` Salesforce ID before that Customer exists in the portal. How is linkage handled? | **Assumption: store `customer_salesforce_id` and set `customer=None`.** When a Customer arrives later, `apply()` queries and links waiting contacts matching `customer_salesforce_id`. | If ordering was strictly required, the Contact webhook would fail with 400 or retry repeatedly, wasting capacity and delaying contact availability. |
| Q20 | DEFAULTED (T-004): an inbound webhook carries `null`/`None` for an optional non-nullable string field. | **Assumption: sanitize `None` to empty string `""`.** Non-nullable CharFields, TextFields, and EmailFields convert `None` to `""` before saving. | If `None` were not sanitized, Django would raise an IntegrityError or ValidationError on save, rejecting valid Salesforce clearance of string fields. |

## 2. Systems, who calls whom, network boundary, interfaces

| Part | Runs where | Job | Status |
|---|---|---|---|
| Salesforce | Client's infrastructure | CRM. Sales users edit here. Has its own database. | Exists. Not ours. |
| Django web | Django 5.2 on the host (`runserver`) | HTTP entry point: the webhook endpoint (path A) and the portal API (Q6). Serves `GET /api/v1/health/` and `POST /api/v1/webhooks/salesforce/` today. | Webhook endpoint built (phase 3, T-004); portal API not built yet. Portal login: DRF token per user (Q14, default); the Salesforce integration user comes from `manage.py create_integration_user`. |
| Dramatiq worker | Container `worker` | Runs Dramatiq actors and the dramatiq-crontab scheduler. | Runs dedupe retention actor `purge_expired_change_events` (daily at 02:00, `@cron` in `src/sync/tasks.py`) and dramatiq-crontab's heartbeat. Outbound and catch-up sync actors not built yet. |
| Postgres 17 | Container `db` | Portal copy of the records. | Running. Six models, migrations `0001_initial` and `0002_audit_columns` (§4). |
| Redis 7 | Container `redis` | Dramatiq broker (task messages), wake-up signal for the outbox, shared rate-limiter count, scheduler lock, health ping. | Running, AOF on. |

Stack in use: Python 3.14, Django 5.2, Django REST Framework, django-dramatiq, dramatiq-crontab,
Postgres 17, Redis 7 (see `pyproject.toml` and `docker-compose.yml`).

Who calls whom (from the brief):

1. Salesforce calls the Django web endpoint with a webhook (path A).
2. The Dramatiq worker calls the Salesforce record API with an outbound call (path B).
3. Sales users talk only to Salesforce. Portal users talk only to the portal.

Network boundary: HTTPS over the public internet is the only path between Salesforce and us. On
our side, the worker reaches `db` and `redis` by service name on the Compose network. The web
process runs on the host and reaches them on the published ports.

Two services (Q6, decided): the web process and the worker share only Postgres and Redis. Web →
worker: outbox rows in Postgres, plus a Redis nudge. Worker → web: nothing direct; results go to
Postgres. Either side can be down or redeployed without losing work.

Interface on each side:

| Path | Caller | Callee | Interface | Payload |
|---|---|---|---|---|
| A. Webhook | Salesforce | Django web endpoint | HTTPS `POST` with DRF token authentication (Q11). `POST /api/v1/webhooks/salesforce/` (`openapi.yml`). | `eventId`, `recordId`, `objectType`, `occurredAt`, `changedBy`, `changedFields` (brief sample) |
| B. Outbound call | Dramatiq worker | Salesforce record API | HTTPS update via Salesforce's documented record API; creates as upsert by external ID (assumption Q13) | The fields the portal user changed, at their current values (§6). Creates also carry our record ID as the external ID. |
| Health | Operator | Django web | `GET /api/v1/health/` (public): 200 if Postgres and Redis both answer, else 503 | JSON: `postgres`, `redis` |

URL conventions (decided by the user): `config/urls.py` is the root URLconf and the only place that
defines `api/v1/` (it includes `utils.urls`, then `sync.urls`, plus `admin/`). Resource names are
singular (`api/v1/customer/`, `api/v1/customer/{id}/history/`). Every app has `urls.py` (with
`app_name`, never repeating the prefix) and `serializers.py`. The contract is [`openapi.yml`](openapi.yml).

Authentication: DRF `TokenAuthentication` only, `IsAuthenticated` by default, so a missing or bad token
gets 401. Health opts out with `AllowAny`. The Salesforce integration user is in the group
`salesforce-integration` (permission class `IsSalesforceIntegration`); create it and print its token with
`uv run python src/manage.py create_integration_user`.

## 3. Subproblems, in build order

Done (phase 0): Compose stack with Postgres, Redis (AOF on) and the worker; Django project; health endpoint;
worker reload override. Phase 1 done (T-001, T-002): models, audit columns, auth, admin, URL layout,
serializers, skills and the chat-export change. Phase 2 built (T-003): `apply()`, with its tests. Phase 3
built (T-004): `WebhookView`, `WebhookEventSerializer`, customer linkage, and dedupe retention task (160 tests passing,
8 marked `brief`). Not built: phases 4 to 9. Full phases, tasks and subtasks: [`build-plan.md`](build-plan.md).

Riskiest first:

| # | Subproblem | Why at this point |
|---|---|---|
| 1 | **Foundations:** apps, models, admin, the API contract | Everything else needs the tables and constraints. The dedupe and outbox guarantees are database constraints, so they come first. |
| 2 | **`apply()`:** duplicate, out-of-order, per-field merge, history, sync loop | **Riskiest.** Every rule the brief checks runs here. If it's wrong, nothing on top can be right. It needs no network, so it's the cheapest place to prove the rules. |
| 3 | **Inbound webhook** (path A) | A thin, checked entry into `apply()`. Built in phase 3 (T-004). |
| 4 | **Test scenarios:** a fake Salesforce inside the test suite (no extra app or container) | Makes outages, 429s, rejections, duplicates and echoes reproducible before building outbound. The brief: *"call a fake CRM you write yourself"*. |
| 5 | **Outbound and the outage** (path B): limiter, retries, down detection, restart safety | **Second riskiest.** This is where work gets lost if we're wrong: the 6-hour outage, retries counted in the cap, surviving restarts. |
| 6 | **Catch-up** (path C) | Depends on 4 and 5. Recovers webhooks missed during an outage. |
| 7 | **Portal API** | Generic views over `apply()`; low risk |
| 8 | **Demo** | The brief's four checks end to end, with a visible cap |

## 4. Data we store

| Store | What it holds | Where it is set |
|---|---|---|
| Postgres 17 (portal database) | Portal copy of customers and contacts, with the history, the dedupe IDs, the outbox and the sync state (watermark, up/down). Column layout below. | `src/config/settings.py` (`DATABASES`) |
| Redis 7, AOF on | Dramatiq broker task messages. Wake-up signal for the outbox. Shared rate-limiter count. Scheduler lock. No retry delays for outbound calls (Q7). | `docker-compose.yml`, `src/config/settings.py` (`DRAMATIQ_BROKER`, `DRAMATIQ_CRONTAB`) |

Fields in scope (brief):

- Customer: name, status, owner.
- Contact: full name, phone, email, city, owner, and the customer it belongs to.

Column layout (app `sync`, as built in migrations `0001_initial` and `0002_audit_columns`). The API field names are in
[`openapi.yml`](openapi.yml). Customer and Contact share an abstract base, `SyncRecord`.

**Customer**

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | Portal ID |
| `external_id` | UUID, unique, default `uuid4` | Sent to Salesforce as the external ID for upserts (Q13) |
| `salesforce_id` | varchar(18), unique, null | Null until Salesforce has the record. Several nulls coexist. |
| `name` | varchar(255) | |
| `status` | varchar(40) | Free text, as Salesforce sends it |
| `owner` | varchar(255) | Salesforce user ID or name |
| `field_meta` | JSONB, default `{}` | Per field: `at` (time of the current value), `source`, `by`, `sf_value` (last value synced from Salesforce; the changed-field check, Q2). Set by a Salesforce or catch-up change, and kept as is by a portal edit. Absent until Salesforce has sent that field. |
| `created_at`, `updated_at` | timestamptz | `auto_now_add` / `auto_now` |
| `created_by`, `updated_by` | varchar(255), default empty | Who first set the record and who last changed it: a portal user, or the Salesforce `changedBy`. Set by `apply()` (built in phase 2, T-003): `created_by` only on create, `updated_by` on every change that applied a field (not on a duplicate, no-change or echo). The per-field trail stays in `field_change.actor` and `field_meta.by`; these two answer "who created this?" without a lookup. Returned by the API as `createdBy` / `updatedBy`. |

**Contact**: the same sync columns (`id`, `external_id`, `salesforce_id`, `field_meta`,
timestamps, `created_by`, `updated_by`), plus `full_name` varchar(255), `phone` varchar(40), `email` varchar(254, indexed),
`city` varchar(120), `owner` varchar(255), `customer` (FK to Customer, null, indexed, `PROTECT`,
related name `contacts`) and `customer_salesforce_id` (varchar(18), indexed, default empty string).
The last one holds the customer's Salesforce ID until that customer reaches the portal; the FK is
linked when it arrives.

**ChangeEvent** (dedupe, §8): `event_id` varchar(128), **database-unique** through the constraint
`change_event_event_id_uniq`; `source` varchar(40); `received_at` (indexed, for the 7-day purge).

**FieldChange** (history, §7, append-only): `object_type` varchar(20), `record_id` **bigint**
(not a foreign key, since it points at either table), `field` varchar(64), `value` (text, null),
`actor` varchar(255), `source` varchar(40), `occurred_at`, `status` varchar(12) (`applied` |
`superseded` | `echo`), `event_id` varchar(128, default empty), `created_at`. Index
`field_change_record_idx` on `(object_type, record_id, occurred_at)`.

**Outbox** (§6): `object_type` varchar(20), `record_id` **bigint**, `operation` varchar(10)
(`create` | `update`), `fields` (Postgres **array** of varchar(64) names, `ArrayField`, default
empty; values are read at send time), `state` varchar(12) (`pending` | `sent` | `needs_review`,
default `pending`), `attempts` (positive integer, default 0), `next_attempt_at` (no default: the
writer sets it), `lease_until` (null), `last_error` (text, default empty), `created_at`,
`sent_at` (null). **At most one `pending` row per record**: partial unique constraint
`outbox_one_pending_per_record` on `(object_type, record_id)` where `state = 'pending'`, so
edits merge at write time. Index `outbox_state_next_idx` on `(state, next_attempt_at)`.

**SyncState**: `key` varchar(64) (PK), `value` (JSONB, default `{}`), `updated_at`. Holds the
catch-up watermark, the Salesforce up/down state and the consecutive-failure count.

## 5. Inbound path: webhook to our database

Built (phases 2 and 3, ledgers T-003 and T-004): `WebhookView` at `/api/v1/webhooks/salesforce/`,
`WebhookEventSerializer`, token authentication (`IsSalesforceIntegration`), customer linkage (Contact `customer_salesforce_id`
and Customer arrival), step 4 inside `apply()`'s record lock, step 5 `apply()` itself, and HTTP 200 response. Brief: "Our endpoint has to accept it, check it, and apply it before anything is
stored." Webhooks can arrive twice and out of order, and `occurredAt` is Salesforce's time for
the event.

Decided. Steps 1 to 4 run before anything is stored. Step 5 is the first write.

1. **Accept:** HTTPS `POST` to the webhook endpoint. The URL is `/api/v1/webhooks/salesforce/` (`openapi.yml`).
2. **Authenticate (Q11):** DRF `TokenAuthentication`. A missing or wrong token gets **401** and
   nothing is stored. The token belongs to a dedicated integration user, and a permission check
   limits that user to this endpoint.
3. **Validate:** `WebhookEventSerializer` validates required fields: `eventId` (max 128),
   `recordId` (max 18), `objectType` (enum: `Customer`, `Contact`), `occurredAt` (ISO 8601),
   `changedBy` (max 255), and `changedFields` (dict). Fields outside the brief's scope are ignored.
   A malformed payload or missing required field gets **400** and nothing is stored.
4. **Work out what changed (Q2):** the webhook carries only the changed fields (assumption,
   matches the brief's sample). A field counts as changed only if its value differs from the
   last value we synced from Salesforce — the same comparison that makes the rule correct even
   if a webhook ever carries the whole record instead.
   - **Customer linkage for Contact:** if a Contact webhook carries a `customer` Salesforce ID (`001...`),
     it stores `customer_salesforce_id`. If that Customer already exists in Postgres, `contact.customer`
     is linked; if the Customer has not arrived yet, `contact.customer` is set to `None`. When that
     Customer arrives later via webhook or `apply()`, waiting contacts with matching `customer_salesforce_id`
     are linked automatically.
   - **Data sanitization:** optional non-nullable CharFields with `None` values (e.g. cleared strings)
     are sanitized to empty string `""` before saving.
5. **Apply:** `apply()` runs the duplicate, echo, ordering and history rules (§7 to §9) in one
   transaction.
6. **Respond 200** with `WebhookResult` schema (`{"eventId": ..., "result": outcome, "appliedFields": [...]}`)
   after the commit, also for duplicates (`result="duplicate"`), echoes (`result="echo"`), superseded
   changes (`result="superseded"`), or no-op re-deliveries (`result="no_change"`). It always returns
   HTTP 200 OK (never 201), so Salesforce stops redelivering.

The endpoint runs in the Django web process, separate from the worker (Q6).

## 6. Outbound path: portal edit to Salesforce API call

Decided (Q7). Mechanism: [Architecture](https://github.com/hellosrijan/sync-data/wiki/Architecture), path B.

**The outbox drives sending and retries.** A portal edit that applies at least one field adds an
outbox row in the same transaction (§9). Each row has `state`, `attempts`, `next_attempt_at`,
`last_error` and `lease_until`.

```
pending ──send──► sent ✓
   ▲  │
   │  ├─ unreachable / timeout / 5xx ─► pending again, next_attempt_at = now + backoff
   │  ├─ 429 slow down ───────────────► pending again, at Retry-After; the whole limiter slows
   │  └─ 400-type rejection ──────────► needs_review   (never dropped, alert raised)
   └── a person fixes it and requeues ◄─┘
```

Sending:

- The Dramatiq outbound task is a single pass over due rows. Redis wakes it, and a timer task
  every 5 s backs up a lost wake-up. **Dramatiq's own retries are off for this task**, so no
  backoff of hours and no dead-letter set.
- Workers claim due rows with `SELECT … FOR UPDATE SKIP LOCKED` and set `lease_until`. If a worker
  dies mid-call, the lease runs out and another worker sends the row again.
- **We send the record's current value, not the edit.** The worker reads the current values of
  the changed fields at send time. So an older row can't overwrite a newer value in Salesforce,
  several pending edits to one record collapse into one call, and an update sent twice is
  harmless.
- **Creates carry our record ID as an external ID** and use upsert (assumption Q13). A create
  retried after a timeout then matches the existing record instead of making a duplicate. On
  success we store the Salesforce ID.
- Every call goes through the shared limiter at R = 90 req/s (Q10), retries included.

Each answer from Salesforce:

| Answer | Action |
|---|---|
| Accepted | `sent`. On a create, store the Salesforce ID. |
| Unreachable, timeout, 5xx | Back to `pending`. Retry at 1, 2, 4, 8 s…, capped at 60 s. Four quick retries take 1 + 2 + 4 + 8 = 15 s, inside the 30 s target. |
| 429 slow down | Back to `pending` at `Retry-After`. The shared limiter pauses until then, halves its rate, and climbs back by 5 req/s every 30 s to 90. |
| 400-type rejection | `needs_review` and an alert. Never dropped (Q9). A person fixes it and requeues. |
| 401 / 403 | Stop all sending and page someone. Our credentials are wrong, so every call would fail. |

Salesforce is marked **down** after 5 consecutive unreachable failures. Sending stops and a probe
runs every 10 s. When the probe succeeds, catch-up runs, then every pending row is due at once
(§10).

The outbox model is built (task 1.3.2), and `apply()` writes its rows (phase 2). Not built yet: the outbound task, the limiter and the probe. The fields in an
outbound call are the fields the portal user changed, read at their current values.
`src/config/settings.py` already turns on Dramatiq's `Retries` middleware for every actor
(`max_retries` 20 by default). The outbound task must set `max_retries=0`.

## 7. Rule: both sides change the same record

Decided. Full mechanism: [Architecture](https://github.com/hellosrijan/sync-data/wiki/Architecture), `apply(change)`.

- **Per field, the newest `occurred_at` wins.** On a webhook that's `occurredAt`. On a portal
  save it's our server clock when the save arrives (the portal sample has no time field), and
  the actor is the logged-in user (answers Q5).
- Each record stores the last-applied time and source of every field. A change is applied only
  if its `occurred_at` is newer than that field's stored time. Edits to different fields
  therefore both survive.
- **Every edit is written to the `field_change` history**, whether applied or superseded, inside
  the same transaction. It isn't a Django signal: signals miss superseded edits and bulk writes.
- Concurrent edits to one record are serialised with `SELECT ... FOR UPDATE`.
- **Exact tie: Salesforce wins.**
- **Two portal users, same field (Q4):** both saves apply, and the later one is current. There's no conflict check, so the earlier editor isn't warned. Both edits stay in history.
- *Assumption:* Salesforce's clock and ours are NTP-synced, with drift well below the gap between
  real edits. *If not:* a later real edit can be ranked as earlier and recorded as superseded.

## 8. Rule: duplicate webhook, and an older webhook after a newer one

Decided.

- **Duplicate:** `change_event.event_id` is unique. The key is `eventId` for a webhook, a new
  UUID for a portal save, and `record_id` + Salesforce modified time for a catch-up result. In
  `apply()`, inserting into `change_event` runs in a nested savepoint (`transaction.atomic()`);
  a repeat hits the unique constraint, returning `outcome="duplicate"` without poisoning the
  transaction or persisting any changes.
- **Dedupe retention: 7 days.** *Assumption:* Salesforce redelivers within 7 days. *If not:* a repeat
  that arrives after its ID has expired is not caught as a duplicate. The per-field time check still
  stops it from overwriting a newer value, but it adds a second history row. Built in phase 3 (T-004):
  a daily dramatiq-crontab actor `purge_expired_change_events` (`@cron("0 2 * * *")` in `src/sync/tasks.py`)
  deletes `ChangeEvent` records where `received_at < now() - 7 days`.
- **Older after newer:** per field, an `occurred_at` older than the field's stored time is
  written to history as *superseded*. It never becomes the current value. The earlier edit stays
  on record, as the brief requires.

## 9. Rule: stopping the sync loop

Decided.

- Only a **portal** change that applied at least one field creates an outbox row. Webhook and
  catch-up changes never do, so nothing received from Salesforce is ever sent back.
- **Our echo:** a webhook whose `changedBy` is our integration user (`settings.SALESFORCE_INTEGRATION_USER`,
  a placeholder until set in `.env`; catch-up results count too) is our own update coming
  back. It's recorded as a confirmation and never applied: one `echo` history row per field, and the
  `ChangeEvent` is kept. No record column, `field_meta` or outbox row changes. This stops a delayed echo from
  overwriting a newer portal edit. An echo never creates a record (Q17).
- *Assumption:* `changedBy` shows our integration user for writes made through the API. *If not:* our
  own echo is applied like any Salesforce change. It is still not sent back, because webhook changes
  never create outbox rows.

## 10. The 6-hour outage

Mechanism: [Architecture](https://github.com/hellosrijan/sync-data/wiki/Architecture), "After an
outage".

What is on disk:

| Item | Where | Note |
|---|---|---|
| Pending calls (portal edits not yet in Salesforce) | Postgres `outbox` | Committed in the same transaction as the edit. Survive any restart of ours. Never dropped (Q9). |
| Portal records and history | Postgres | Not affected by the outage. |
| Wake-up signal for the worker | Redis | Losing it loses no work; the outbox still holds every pending call. |
| Webhooks missed during the outage | Not on our disk | Recovered by the catch-up query (path C, assumption Q3). |

How it drains:

1. While Salesforce is down, outbound calls fail and their outbox rows stay pending. A probe
   checks every ~10 s whether Salesforce is back.
2. On recovery, catch-up runs first: it fetches what changed in Salesforce since the watermark and
   applies it. Same-field conflicts are settled before we send anything. Catch-up also runs every 5 min as a safety net (path C, Architecture).
3. Then the outbox drains at R = 90 req/s through the shared limiter. Several edits to one record
   are merged into one call.

Retry policy (Q7, §6): the outbox drives retries at the limiter's pace, and no wait is longer
than 60 s. Dramatiq's backoff isn't used, so the drain figures below hold. Recovery is noticed
within 10 s (the probe). Catch-up then takes 54 calls, under 1 s, before the drain starts.

Arithmetic. Symbols:

- T = outage length = 6 h = 21,600 s.
- r = portal edits per second during the outage. **Assumed 5/s at peak (Q1).**
- R = run rate = **90 req/s (Q10)**, under the cap C = 100.
- B = backlog = r × T. One outbound call per edit (merging only makes it smaller).

Worst case: the outage covers 6 hours of peak, and new edits keep arriving at r while we drain.

- B = 5 × 21,600 = **108,000 calls**
- Catch-up first: 108,000 Salesforce-side changes ÷ 2,000 records per query = **54 calls**, < 1 s
- Drain: B ÷ (R − r) = 108,000 ÷ (90 − 5) = 108,000 ÷ 85 = **1,271 s ≈ 21.2 min**

Other cases, same formula:

| Case | r (portal edits/s) | Backlog B | Division | Drain |
|---|---|---|---|---|
| Worst case: 6 h of peak | 5 | 108,000 | 108,000 ÷ 85 | **21.2 min** |
| Normal working day | 1.75 | 37,800 | 37,800 ÷ 88.25 | **7.1 min** |
| Overnight (quiet hour) | 0.1 | 2,160 | 2,160 ÷ 89.9 | **24 s** |

The r column is the portal share, half of the total in Q1. Working hours average 3.5 edits/s in
total, so 1.75 portal; the quiet hour is 0.2 in total, so 0.1 portal.

Sensitivity: drain minutes = 21,600 · r ÷ (90 − r) ÷ 60 = 360 · r ÷ (90 − r).

| r | Drain after 6 h at that rate |
|---|---|
| 5 (assumed) | 21 min |
| 10 | 45 min |
| 20 | 1.7 h |
| 45 | 6 h (as long as the outage) |
| ≥ 90 | never drains |

The design has about 9× headroom over the assumed portal peak before draining takes as long as
the outage.

Full reconcile, for comparison (Q3): 5,000,000 records ÷ 2,000 per query = 2,500 calls ÷ 90 req/s
≈ **28 s** of cap. Without a query API, one record per call: 5,000,000 ÷ 90 ≈ 15.4 h.

## 11. Three numbers to watch

| Metric | Page someone at |
|---|---|
| Portal edits per second (the rate that fills the outbox) | **30/s sustained for 5 min.** That's 6× the assumed peak (Q1); at 30/s a 6-hour backlog takes 3 h to drain, and at 45/s draining takes as long as the outage. |
| Age of the oldest `pending` outbox row, while Salesforce is up | **> 2 min.** The 30 s sync target is clearly broken. Excludes outages, when rows are expected to wait. |
| Rows in `needs_review` | **> 0 for 1 h.** Data Salesforce rejected and we can't send. It waits on a person (Q9). |

## 12. Left out of this weekend's work

Out of scope per the brief: deletes, Salesforce login screens, a visual UI, a second region, a
load test against real Salesforce.

Left out for now:

- Bulk or mass updates (Q12). The brief doesn't mention them. We assume interactive edits only,
  so there's no outbox priority and no batch API.

- CI runs only the wiki publish (`.github/workflows/wiki.yml`). Tests are not run in CI yet.
- Optional code language: the brief says TypeScript on Node.js 20 or newer. This project's code
  is Python 3.14 with Django. This is the user's decision, recorded as a deviation. It is not a
  design choice.

Built in scope: inbound webhook endpoint and dedupe retention (phase 3, T-004), `apply()`
rules core (phase 2, T-003), foundations, models, admin and auth (phase 1, T-001, T-002).
Not built yet (in scope, not left out): FakeSalesforce test double and scenarios (phase 4),
outbound actors, the shared rate limiter, the outage probe (phase 5), catch-up (phase 6),
portal API views (phase 7), and the demo script (phase 8). The sync rules themselves in §5 to §9
are built at the database level in `apply()` (`src/sync/services.py`, phase 2, T-003) and exposed
inbound via `WebhookView` (`src/sync/views.py`, phase 3, T-004).

TODO: anything else we cut.
