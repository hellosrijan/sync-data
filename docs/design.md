# Design: two-way sync of customer and contact records

Source of truth: [`Round1.html`](Round1.html). Section order follows the brief's outline.
Status: **skeleton**. Phase 0 is built: Compose stack (db, redis, worker), Django project, and
`GET /health/`. No models or sync logic yet. Sections marked TODO have no decision yet.

Targets every section must close against: 5 million records; ≤ 30 s sync while both sides are
up; ≤ 100 requests/sec to the Salesforce record API, retries included; a 6-hour Salesforce
outage with no lost work.

Terms, used the same way in every section:

- **Webhook**: an HTTPS request from Salesforce to our endpoint. Carries the brief's sample fields:
  `eventId`, `recordId`, `objectType`, `occurredAt`, `changedBy`, `changedFields`.
- **Outbound call**: an HTTPS request from our worker to the Salesforce record API (create or update).
- **Pending call**: an outbound call that is queued and not yet sent. It is one Dramatiq message in Redis.
- **Portal edit**: a save in the portal by a finance or customer-success user.

## 1. Assumptions and open questions

Each answer needs: the assumption we take, and one sentence on what changes if it goes the
other way.

| # | Question (from the brief) | Assumption | If the answer is the other way |
|---|---|---|---|
| Q1 | Peak edits/sec, and edits/sec in a quiet hour? | TODO | TODO |
| Q2 | Does a webhook carry only the changed fields, or the full record? What does the rule do in the other case? | TODO | TODO |
| Q3 | Can we ask Salesforce for records changed during the 6-hour outage, or are missed webhooks gone? | TODO | TODO |
| Q4 | Two portal users edit the same field: what does the other user see, and what do we send to Salesforce? | TODO | TODO |
| Q5 | A portal edit has no time field: what do we trust for order, and where do the time and the actor come from? | TODO | TODO |

Questions added while writing this doc (not in the brief):

| # | Question | Assumption | If the answer is the other way |
|---|---|---|---|
| Q6 | Which process hosts the webhook endpoint, and what crosses from it to the sync worker? The config has two places today: Django web (on the host) and the worker container (Dramatiq and the cron scheduler, two processes). | TODO | TODO |
| Q7 | Outbound retry policy during an outage. The worker uses Dramatiq's default backoff (see §6), so a pending call can wait hours after Salesforce is back. The brief says drain as fast as the cap allows. Which policy? | TODO | TODO |
| Q8 | Redis AOF is on, but the fsync setting is Redis's default (`everysec`). A host crash can lose about 1 s of writes. Is that acceptable for pending calls, or do we set `appendfsync always`? | TODO | TODO |
| Q9 | After 20 retries (21 failed attempts in all), Dramatiq marks a pending call failed and stops retrying. It moves to Redis's dead-letter set, which expires it after 7 days (`dead_message_ttl`, library default). Do we drop it, or keep it for a person to look at? | **Decided (user): never dropped.** Losing a failed call after 7 days is not acceptable. A pending call that runs out of retries is kept until someone resolves it. How it is kept is TODO. | If dropping were acceptable, Dramatiq's default would do. We would need no store and no review step for failed calls, but the brief's "keep the work" would not hold. |
| Q10 | How far under the 100 req/s cap do we run (the brief says we choose), and how much headroom do we keep for retries? | TODO | TODO |
| Q11 | How do we check that a webhook really came from Salesforce? The brief does not say. | TODO | TODO |

## 2. Systems, who calls whom, network boundary, interfaces

| Part | Runs where | Job | Status |
|---|---|---|---|
| Salesforce | Client's infrastructure | CRM. Sales users edit here. Has its own database. | Exists. Not ours. |
| Portal web | Django 5.2 on the host (`runserver`) | HTTP entry point. Serves `GET /health/`. Webhook endpoint would run here or in the worker (Q6). | Health check only. Webhook endpoint not built. |
| Portal worker | Container `worker` | Runs Dramatiq actors and the dramatiq-crontab scheduler. | Heartbeat actor only. No sync actors yet. |
| Postgres 17 | Container `db` | Portal copy of the records. | Running. No models yet. |
| Redis 7 | Container `redis` | Dramatiq broker (pending calls), scheduler lock, health ping. | Running, AOF on. |

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
| Postgres 17 (portal database) | Portal copy of customers and contacts. Tables: TODO. | `src/config/settings.py` (`DATABASES`) |
| Redis 7, AOF on | Dramatiq broker: pending calls, and retries waiting on a delay. Scheduler lock. | `docker-compose.yml`, `src/config/settings.py` (`DRAMATIQ_BROKER`, `DRAMATIQ_CRONTAB`) |

Fields in scope (brief):

- Customer: name, status, owner.
- Contact: full name, phone, email, city, owner, and the customer it belongs to.

Not decided: the history table (who changed what, and when), and the table that records webhooks
already seen (for duplicates). TODO.

## 5. Inbound path: webhook to our database

Not built.

Requirements from the brief (not yet decisions):

- Accept, check and apply a webhook before anything is stored.
- A webhook can arrive twice, and out of order.
- `occurredAt` is the time Salesforce gives the event.
- Whether a webhook carries changed fields only (Q2) is TODO.

Decisions: TODO.

## 6. Outbound path: portal edit to Salesforce API call

What the config already does:

- Outbound calls will run as Dramatiq actors in the `worker` container. No actor is written yet.
  The worker runs 1 process with 2 threads.
- The queue is Redis, with AOF on. A pending call stays in Redis until a worker takes it.
- Retries use Dramatiq's `Retries` middleware with library defaults:
  - max 20 retries;
  - delay 15 s × 2^(retry − 1), with jitter up to 2×, capped at 7 days.
  - **Conflicts with Q9.** After the last retry, the default puts a call in the dead-letter set,
    which expires it after 7 days. Change this before any outbound actor ships.
- Time limit per message: 10 minutes (Dramatiq default).
- Age limit: none. Pending calls do not expire by age.

Not built: the rate cap. No code enforces 100 req/s yet. Two worker threads are not a cap.

Decisions: TODO. That includes how a portal edit becomes a pending call, which fields go in an
outbound call, and the run rate (Q10). Retry policy is Q7.

## 7. Rule: both sides change the same record

TODO. Name the field that decides which edit is current, on a webhook and on a portal save.
Brief requirement: one current value per field, and history kept for every edit.

## 8. Rule: duplicate webhook, and an older webhook after a newer one

TODO. Name the key that finds a duplicate, the field that orders edits (`occurredAt` on a
webhook, see Q5 for a portal edit), the comparison, and the action.

## 9. Rule: stopping the sync loop

TODO. Brief requirement: an update we send to Salesforce must not come back and bounce again.

## 10. The 6-hour outage

What is on disk:

| Item | Where | Note |
|---|---|---|
| Pending calls | Redis, AOF on | Survive a Redis or worker restart. Fsync is Redis's default `everysec` (Q8). |
| Portal records | Postgres | Not affected by the outage. |
| Webhooks missed during the outage | Not on our disk | The brief says they may never arrive (Q3). |

Mechanism already in place: when a call fails, Dramatiq re-queues it with a delay. Pending calls
are not dropped by age. If a worker dies mid-call, the Redis broker re-queues the message after
`heartbeat_timeout` (60 s, Dramatiq default, `dramatiq/brokers/redis.py`).

How it drains: TODO. Depends on Q7.

Arithmetic. Symbols:

- T = outage length = 6 h = 21,600 s.
- r = edits per second during the outage. Not answered yet (Q1), so kept as a symbol.
- C = cap = 100 requests/s.
- R = run rate, R ≤ C. Not chosen yet (Q10). Headroom = C − R.
- B = backlog = r × T = 21,600 r. Assumes one outbound call per edit.

Drain time, with no new edits: B ÷ R. At R = 100: 21,600 r ÷ 100 = 216 r s = **3.6 r min**.

Drain time, with new edits still arriving at r: B ÷ (R − r). At R = 100:
21,600 r ÷ (100 − r) s = **360 r ÷ (100 − r) min**. Needs r < 100, or the backlog never drains.

Illustrative values only. These are not the assumed rate.

| r (edits/s) | Backlog B (edits) | Drain minutes at R = 100, new edits continuing |
|---|---|---|
| 1 | 21,600 | 360 ÷ 99 = 3.6 |
| 5 | 108,000 | 1,800 ÷ 95 = 18.9 |
| 10 | 216,000 | 3,600 ÷ 90 = 40.0 |
| 50 | 1,080,000 | 18,000 ÷ 50 = 360 |

Retry delay caveat: with the default backoff, the drain times above are a floor, not an estimate.
The 11th retry waits 15 s × 2^10 = 15,360 s (4.3 h). With jitter it can take up to 8.5 h. A
pending call whose next attempt falls in that step waits hours after Salesforce is back. Q7 has to
close this before the drain figures can be trusted.

Full reread, for comparison (relevant to Q3): 5,000,000 records ÷ 100 req/s = 50,000 s = 833 min
≈ 13.9 h, assuming one record per call. A full reread cannot be the outage recovery plan if the
drain must finish in minutes.

## 11. Three numbers to watch

| Metric | Page someone at |
|---|---|
| TODO | TODO |
| TODO | TODO |
| TODO | TODO |

## 12. Left out of this weekend's work

Out of scope per the brief: deletes, Salesforce login screens, a visual UI, a second region, a
load test against real Salesforce.

Left out for now:

- CI runs only the wiki publish (`.github/workflows/wiki.yml`). Tests are not run in CI yet.
- Optional code language: the brief says TypeScript on Node.js 20 or newer. This project's code
  is Python 3.14 with Django. This is the user's decision, recorded as a deviation. It is not a
  design choice.

Not built yet (in scope, not left out): the webhook endpoint, outbound actors, the rate cap, the
models, and the sync rules in §5 to §9.

TODO: anything else we cut.
