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

Phase 0: project skeleton (Django, Postgres, Redis, Dramatiq worker). No data model or sync
logic yet. The design is in progress on the [Design](Design) page.

## Pages

| Page | What's in it |
|---|---|
| [Getting Started](Getting-Started) | Setup, running the stack, the worker, layout |
| [Architecture](Architecture) | Draft architecture: who calls whom, the diagram, open points |
| [Design](Design) | The design document: assumptions, rules, outage arithmetic |
| [Development Workflow](Development-Workflow) | Commit rules, Claude agents, `/ready-to-commit`, how this wiki is published |

## About this wiki

This wiki is built from the repository by CI on every push to `main`. **Don't edit pages
here.** Changes made in the GitHub UI are overwritten on the next publish. See
[Development Workflow](Development-Workflow#how-this-wiki-is-published).
