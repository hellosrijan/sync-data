# sync-data

Two-way sync of customer and contact records between Salesforce (the client's CRM) and the
Tequity operations portal (Postgres). This is Tequity's Round 1 exercise; the brief,
[`docs/Round1.html`](https://github.com/hellosrijan/sync-data/blob/main/docs/Round1.html), is
the source of truth.

Targets every decision has to meet:

| Constraint | Value |
|---|---|
| Records (customers + contacts) | 5 million |
| Sync while both sides are up | ≤ 30 s worst case |
| Calls to the Salesforce record API | ≤ 100 requests/sec, retries included |
| Salesforce outage to survive | 6 hours, no lost work |

## Status

Phases 0 to 8 are built: the foundations (models, audit columns, admin, token authentication, URL
layout, serializers), `apply()` (`src/sync/services.py`), the inbound webhook endpoint (`POST /api/v1/webhooks/salesforce/`),
customer linkage, dedupe retention task, `FakeSalesforce` test double, scenario builders, `SalesforceClient`,
Redis token-bucket rate limiter, outbound sync worker (`send_outbound`), down detection, recovery probe,
catch-up sync (`run_catchup`), portal REST API endpoints (`/api/v1/customer/`, `/api/v1/contact/`, `/api/v1/sync/status/`),
and single-command demo script (`scripts/demo.sh`) (201 tests passing, 11 marked `brief`).

## Pages

| Page | What's in it |
|---|---|
| [Getting Started](Getting-Started) | Setup, running the stack, the worker, layout |
| [Architecture](Architecture) | Agreed architecture: the three paths, the diagram, the services, the outage flow |
| [Design](Design) | The design document: assumptions, rules, outage arithmetic |
| [Build Plan](Build-Plan) | Phases, tasks and subtasks to build it, riskiest first; the API contract ([`openapi.yml`](https://github.com/hellosrijan/sync-data/blob/main/docs/openapi.yml)) |
| [Development Workflow](Development-Workflow) | Commit rules, Claude agents, `/ready-to-commit`, how this wiki is published |

## About this wiki

This wiki is built from the repository by CI on every push to `main`. **Don't edit pages
here.** Changes made in the GitHub UI are overwritten on the next publish. See
[Development Workflow](Development-Workflow#how-this-wiki-is-published).
