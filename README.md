# sync-data

Two-way sync of customer and contact records between Salesforce (the client's CRM) and the
Tequity operations portal (Postgres). Salesforce calls us with webhooks; we call the Salesforce
record API from a background worker, with a target cap of 100 requests/sec (built in phase 5 via
Redis token-bucket rate limiter at 90 req/s). Design targets: 5 million
records, a 30-second sync window while both sides are up, and surviving a 6-hour Salesforce outage.
The brief is in [`docs/Round1.html`](docs/Round1.html).

Status: phases 0 to 8 are built. The models, admin, token authentication, URL layout, serializers,
`apply()` (`src/sync/services.py`), the inbound webhook endpoint (`POST /api/v1/webhooks/salesforce/`),
customer linkage, dedupe retention task, `FakeSalesforce` test double, scenario builders, `SalesforceClient`,
Redis token-bucket rate limiter, outbound sync worker (`send_outbound`), down detection, recovery probe,
catch-up sync (`run_catchup`), portal REST API endpoints (`/api/v1/customer/`, `/api/v1/contact/`, `/api/v1/sync/status/`),
and single-command demo script (`scripts/demo.sh`) are built (201 tests passing, including 11 marked `brief`).
The build plan and its checklist are in [`docs/build-plan.md`](docs/build-plan.md).

## Stack

- Python 3.14, [uv](https://docs.astral.sh/uv/)
- Django 5.2 LTS, Django REST Framework (token authentication)
- Postgres 17 (portal database), Redis 7 (Dramatiq broker for the outbound worker)
- django-dramatiq, dramatiq-crontab (scheduler for `@cron` actors), django-environ
- psycopg 3 (Postgres driver)
- httpx (HTTP client for the Salesforce record API)

## Prerequisites

- uv 0.13+ (installs Python 3.14 for you)
- Docker with Docker Compose v2

## Setup

```sh
cp .env.example .env          # then set DJANGO_SECRET_KEY and POSTGRES_PASSWORD
uv sync                       # creates .venv and installs dependencies
docker compose up -d --build --wait   # Postgres, Redis and the worker
uv run python src/manage.py migrate
uv run python src/manage.py runserver
```

The server listens on http://127.0.0.1:8000. `GET /api/v1/health/` returns 200 when Postgres and
Redis both answer.

### API token for Salesforce

Every API path starts with `/api/v1/` and every endpoint except health needs a token
(`Authorization: Token <key>`). To create the Salesforce integration user (group
`salesforce-integration`) and print its token:

```sh
uv run python src/manage.py create_integration_user   # optional: --username <name>; safe to re-run
```

Put the printed token in the webhook's `Authorization` header. Re-running reuses the same user
and token and prints the same key again.

### Worker

The `worker` container runs two processes:

- `manage.py rundramatiq --reload --reload-use-polling --processes 1 --threads 2`, which
  executes Dramatiq actors and restarts when a `.py` file under `src/` changes (`src/` is
  bind-mounted).
- `manage.py crontab` (dramatiq-crontab), which enqueues actors decorated with `@cron(...)` /
  `@interval(...)`. A Redis lock keeps it to one scheduler.

If either process exits, the container exits and `restart: unless-stopped` brings both back.
`docker compose logs -f worker` shows a heartbeat line every minute when both are healthy (dramatiq-crontab's
heartbeat actor). The sync app's daily dedupe retention actor (`purge_expired_change_events`, daily at 02:00),
outbound sync worker (`send_outbound`), recovery probe (`probe_salesforce`), rate recovery actor (`recover_limiter_rate`),
backup sweep (`sweep_outbound`), and catch-up sync actors (`run_catchup`, `scheduled_catchup` every 5 min) run here.

Put actors in `<app>/tasks.py`; both commands discover them automatically. After changing
dependencies, rebuild with `docker compose up -d --build worker`.

All configuration comes from `.env` (see `.env.example`). Docker Compose reads the same file, so
the Postgres credentials and ports are defined once.

## Tests

```sh
docker compose up -d --wait   # the health tests use the real Postgres and Redis
uv run pytest -q              # runs all 201 tests
uv run pytest -m brief        # run tests for the brief's required checks (11 tests)
bash scripts/demo.sh          # single-command end-to-end demo at SALESFORCE_RATE_LIMIT=5
```

pytest-django creates a separate test database, so the tests never touch your data.
Salesforce is faked inside the test suite (`src/sync/tests/fakes.py`, build plan phase 4)
with no extra app or container.

## Layout

```
src/
  manage.py
  config/      Django project: settings, urls, wsgi/asgi
  sync/        the sync app: models (Customer, Contact, ChangeEvent, FieldChange, Outbox,
               SyncState), admin, serializers, permissions, services (apply() and rules core),
               views (WebhookView, CustomerListCreateView, CustomerDetailView,
               CustomerHistoryListView, ContactListCreateView, ContactDetailView,
               ContactHistoryListView, SyncStatusView), pagination (RecordCursorPagination,
               HistoryCursorPagination), client (SalesforceClient), limiter (RateLimiter),
               tasks (purge_expired_change_events, send_outbound, probe_salesforce,
               run_catchup, scheduled_catchup, recover_limiter_rate, sweep_outbound),
               urls, create_integration_user command, tests (including fakes.py, scenarios.py,
               test_fakes.py, test_client.py, test_limiter.py, test_outbound.py, test_catchup.py,
               test_portal_api.py)
  utils/       shared bits: api/v1/health/ endpoint (views, urls, serializers), rundramatiq
               override (fixes --reload), tests
docs/          Round1.html (the brief), design.md (design doc), build-plan.md (phases and
               checklist), openapi.yml (API contract), chats/ (AI chat exports),
               tasks/ (agent-team task ledgers)
wiki/          hand-written GitHub wiki pages; CI builds and publishes them
scripts/       build-wiki.sh (wiki build), demo.sh (end-to-end brief checks demo under rate limit 5 req/s)
.github/       workflows/wiki.yml: publishes the wiki on push to main
.claude/       Claude Code agents, commands, rules and skills (the AI workflow source of truth)
.opencode/     opencode config, agents and commands: thin wrappers around .claude/
docker-compose.yml   Postgres + Redis + worker; Django's web server runs on the host
Dockerfile           worker image (uv, Python 3.14)
pyproject.toml, uv.lock   dependencies (managed with uv)
.env.example         template for .env
```

## Stopping

```sh
docker compose down           # keeps data in the named volumes
docker compose down -v        # also deletes the volumes
```
