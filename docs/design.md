# Design: two-way sync of customer and contact records

Source of truth: [`Round1.html`](Round1.html). Section order follows the brief's outline.
Status: **design in progress**. Phase 0 is built: Compose stack (db, redis, worker), Django project, and
`GET /health/`. No models or sync logic yet. Decided: §7 to §9, Q5 and Q9 in §1, and the first metric
in §11. Q1, Q10 and Q12 are recorded as assumptions, and §10 is arithmetic on them. Everything marked
TODO is still open.

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
| Q1 | Peak edits/sec, and edits/sec in a quiet hour? | **Assumption: peak 10 edits/s** (5 Salesforce, 5 portal); working-hours average 3.5/s; **quiet hour 0.2/s**. Two independent estimates agree: 5M records × 2 % touched per working day ÷ 28,800 s × 3 (peak factor) ≈ 10/s; and about 500 users × 1 save per minute at peak ≈ 8/s. The 50/50 split is deliberately pessimistic, because only portal edits fill the outbox. | Drain time scales as 360 · r ÷ (90 − r) min (§10). Double the portal rate (10/s) gives 45 min. At 45/s draining takes as long as the outage. At 90/s or more the backlog never drains, and we'd need Salesforce's batch API (another assumption). |
| Q2 | Does a webhook carry only the changed fields, or the full record? What does the rule do in the other case? | TODO | TODO |
| Q3 | Can we ask Salesforce for records changed during the 6-hour outage, or are missed webhooks gone? | **Assumption only, not decided:** Salesforce has a query for records changed since time T, returning about 2,000 records per call. §10 relies on it for catch-up. | If there is no such query, changes made in Salesforce during the outage are lost unless a webhook already arrived, and path C cannot recover them. |
| Q4 | Two portal users edit the same field: what does the other user see, and what do we send to Salesforce? | TODO | TODO |
| Q5 | A portal edit has no time field: what do we trust for order, and where do the time and the actor come from? | **Decided:** time = our server clock when the save arrives; actor = the logged-in portal user. See §7. | If the portal sent its own time, a wrong client clock could reorder edits; we'd have to trust or correct it. |

Questions added while writing this doc (not in the brief):

| # | Question | Assumption | If the answer is the other way |
|---|---|---|---|
| Q6 | Which process hosts the webhook endpoint, and what crosses from it to the sync worker? The config has two places today: Django web (on the host) and the worker container (Dramatiq and the cron scheduler, two processes). | TODO | TODO |
| Q7 | Outbound retry policy during an outage. The worker uses Dramatiq's default backoff (see §6), so a pending call can wait hours after Salesforce is back. The brief says drain as fast as the cap allows. Which policy? | TODO | TODO |
| Q8 | Redis AOF is on, but the fsync setting is Redis's default (`everysec`). A host crash can lose about 1 s of writes. Is that acceptable for pending calls, or do we set `appendfsync always`? | TODO | TODO |
| Q9 | After 20 retries (21 failed attempts in all), Dramatiq marks a pending call failed and stops retrying. It moves to Redis's dead-letter set, which expires it after 7 days (`dead_message_ttl`, library default). Do we drop it, or keep it for a person to look at? | **Decided (user): never dropped.** Losing a failed call after 7 days is not acceptable. A pending call that runs out of retries is kept until someone resolves it. How it is kept is TODO. | If dropping were acceptable, Dramatiq's default would do. We would need no store and no review step for failed calls, but the brief's "keep the work" would not hold. |
| Q10 | How far under the 100 req/s cap do we run (the brief says we choose), and how much headroom do we keep for retries? | **Assumption: run rate R = 90 req/s.** 10 req/s is kept for retries, recovery probes and catch-up queries. All of them go through one shared limiter, so the total never exceeds 100. | Running at R = 100 leaves nothing for retries, so a burst of failures would push us over the cap. Running lower (e.g. 50) roughly doubles every drain time. |
| Q11 | How do we check that a webhook really came from Salesforce? The brief does not say. | TODO | TODO |
| Q12 | Are there bulk or mass updates (e.g. reassigning the owner on 50,000 contacts at once)? The brief doesn't mention any. | **Assumption: no.** Edits are interactive, one record per save, at the rates in Q1. | A portal-side bulk update of 50,000 records takes ≈ 9 min at 90 req/s (50,000 ÷ 90 ≈ 556 s), and an interactive edit queued behind it would miss the 30 s target. We'd add two outbox priorities (interactive first), or use Salesforce's batch API (200 records per call: 50,000 ÷ 200 = 250 calls ÷ 90 req/s ≈ 3 s, another assumption). Salesforce-side bulk changes cost nothing, because webhooks aren't capped. |

## 2. Systems, who calls whom, network boundary, interfaces

| Part | Runs where | Job | Status |
|---|---|---|---|
| Salesforce | Client's infrastructure | CRM. Sales users edit here. Has its own database. | Exists. Not ours. |
| Portal web | Django 5.2 on the host (`runserver`) | HTTP entry point. Serves `GET /health/`. Webhook endpoint would run here or in the worker (Q6). | Health check only. Webhook endpoint not built. |
| Portal worker | Container `worker` | Runs Dramatiq actors and the dramatiq-crontab scheduler. | Only dramatiq-crontab's own heartbeat actor runs. No sync actors yet. |
| Postgres 17 | Container `db` | Portal copy of the records. | Running. No models yet. |
| Redis 7 | Container `redis` | Dramatiq broker (messages, retries), wake-up signal for the outbox, scheduler lock, health ping. | Running, AOF on. |

Stack in use: Python 3.14, Django 5.2, Django REST Framework, django-dramatiq, dramatiq-crontab,
Postgres 17, Redis 7 (see `pyproject.toml` and `docker-compose.yml`).

Who calls whom (from the brief):

1. Salesforce calls the portal web endpoint with a webhook (path A).
2. The portal worker calls the Salesforce record API with an outbound call (path B).
3. Sales users talk only to Salesforce. Portal users talk only to the portal.

Network boundary: HTTPS over the public internet is the only path between Salesforce and us. On
our side, the worker reaches `db` and `redis` by service name on the Compose network. The web
process runs on the host and reaches them on the published ports. Whether one service or two
(Q6) is TODO.

Interface on each side:

| Path | Caller | Callee | Interface | Payload |
|---|---|---|---|---|
| A. Webhook | Salesforce | Portal web endpoint | HTTPS request. Method and path: TODO. | `eventId`, `recordId`, `objectType`, `occurredAt`, `changedBy`, `changedFields` (brief sample) |
| B. Outbound call | Portal worker | Salesforce record API | HTTPS create or update, via Salesforce's documented record API | TODO |
| Health | Operator | Portal web | `GET /health/`: 200 if Postgres and Redis both answer, else 503 | JSON: `postgres`, `redis` |

## 3. Subproblems, in build order

Done (phase 0): Compose stack with Postgres, Redis (AOF on) and the worker; Django project; `/health/`;
worker reload override.

Order: TODO. Subproblems the brief requires, not yet ordered:

- Inbound: accept, check and apply a webhook before anything is stored.
- Outbound: send a portal edit to Salesforce under the cap.
- Same-record merge: per field, with kept history.
- Duplicate and out-of-order handling.
- Loop suppression.
- Outage drain.

## 4. Data we store

| Store | What it holds | Where it is set |
|---|---|---|
| Postgres 17 (portal database) | Portal copy of customers and contacts, with the history, the dedupe IDs and the outbox. Tables named so far: Customer and Contact (column layout TODO), `field_change` (§7), `change_event` (§8), `outbox` (§9, §10). The watermark table is named on the Architecture page only. | `src/config/settings.py` (`DATABASES`) |
| Redis 7, AOF on | Dramatiq broker messages, including retries waiting on a delay. Wake-up signal for the outbox. Scheduler lock. | `docker-compose.yml`, `src/config/settings.py` (`DRAMATIQ_BROKER`, `DRAMATIQ_CRONTAB`) |

Fields in scope (brief):

- Customer: name, status, owner.
- Contact: full name, phone, email, city, owner, and the customer it belongs to.

Not decided: the column layout of each table. TODO.

## 5. Inbound path: webhook to our database

Not built.

Requirements from the brief (not yet decisions):

- Accept, check and apply a webhook before anything is stored.
- A webhook can arrive twice, and out of order.
- `occurredAt` is the time Salesforce gives the event.
- Whether a webhook carries changed fields only (Q2) is TODO.

Decided: every change, webhook included, goes through `apply()` (see
[Architecture](https://github.com/hellosrijan/sync-data/wiki/Architecture)). The rules it applies
are in §7 to §9. Still TODO: where the endpoint runs (Q6), and its method, path and authentication
(Q11).

## 6. Outbound path: portal edit to Salesforce API call

What the config already does:

- Outbound calls will run as Dramatiq actors in the `worker` container. No actor is written yet.
  The worker runs 1 process with 2 threads.
- The Dramatiq broker is Redis, with AOF on. A pending call is an outbox row in Postgres (§9, §10),
  so it does not sit in the Redis queue.
- Retries use Dramatiq's `Retries` middleware with library defaults:
  - max 20 retries;
  - delay 15 s × 2^(retry − 1), times a random factor from 1 to 2 (jitter). Above 7 days, the
    delay is drawn from 50% to 100% of 7 days. From about the 16th retry, delays reach the cap.
  - **Conflicts with Q9.** After the last retry, the default puts a call in the dead-letter set,
    which expires it after 7 days. Change this before any outbound actor ships.
- Time limit per message: 10 minutes (Dramatiq default).
- Age limit: none. Pending calls do not expire by age.

Not built: the rate cap. No code enforces 100 req/s yet. Two worker threads are not a cap.

Decided: a portal edit that applies at least one field adds one outbox row (§9). The run rate is
R = 90 req/s (Q10), with the rest of the cap kept for retries, probes and catch-up. Still TODO:
which fields go in an outbound call, the retry policy (Q7), and how failed calls are kept (Q9).

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
- *Assumption:* Salesforce's clock and ours are NTP-synced, with drift well below the gap between
  real edits. *If not:* a later real edit can be ranked as earlier and recorded as superseded.

## 8. Rule: duplicate webhook, and an older webhook after a newer one

Decided.

- **Duplicate:** `change_event.event_id` is unique. The key is `eventId` for a webhook, a new
  UUID for a portal save, and `record_id` + Salesforce modified time for a catch-up result. A
  repeat hits the unique constraint, so we return OK and do nothing.
- **Dedupe retention: 7 days.** *Assumption:* Salesforce redelivers within 7 days. *If not:* a repeat
  that arrives after its ID has expired is not caught as a duplicate. The per-field time check still
  stops it from overwriting a newer value, but it adds a second history row.
- **Older after newer:** per field, an `occurred_at` older than the field's stored time is
  written to history as *superseded*. It never becomes the current value. The earlier edit stays
  on record, as the brief requires.

## 9. Rule: stopping the sync loop

Decided.

- Only a **portal** change that applied at least one field creates an outbox row. Webhook and
  catch-up changes never do, so nothing received from Salesforce is ever sent back.
- **Our echo:** a webhook whose `changedBy` is our integration user is our own update coming
  back. It's recorded as a confirmation and never applied. This stops a delayed echo from
  overwriting a newer portal edit.
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
   applies it. Same-field conflicts are settled before we send anything.
3. Then the outbox drains at R = 90 req/s through the shared limiter. Several edits to one record
   are merged into one call.

Retry policy (Q7) is still open. Dramatiq's default backoff can delay a retry by hours, and by days
once it reaches the 7-day cap (§6). So the drain figures below hold only once retries are driven
from the outbox at the limiter's pace, not by Dramatiq's backoff.

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
| TODO | TODO |
| TODO | TODO |

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

Not built yet (in scope, not left out): the webhook endpoint, outbound actors, the rate cap, the
models, and the sync rules in §5 to §9.

TODO: anything else we cut.
