# sync-data

Two-way sync of customer and contact records between Salesforce (the client's CRM) and the
Tequity operations portal (Postgres). Salesforce calls us with webhooks; we call the Salesforce
record API from a background worker, with a target cap of 100 requests/sec (the cap is not built
yet). Design targets: 5 million
records, a 30-second sync window while both sides are up, and surviving a 6-hour Salesforce outage.
The brief is in [`docs/Round1.html`](docs/Round1.html).

Status: phase 0 (project skeleton only, no models or sync logic yet).

## Stack

- Python 3.14, [uv](https://docs.astral.sh/uv/)
- Django 5.2 LTS, Django REST Framework
- Postgres 17 (portal database), Redis 7 (Dramatiq broker for the outbound worker)
- django-dramatiq, dramatiq-crontab (scheduler for `@cron` actors), django-environ
- psycopg 3 (Postgres driver)

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

The server listens on http://127.0.0.1:8000. `GET /health/` returns 200 when Postgres and Redis
both answer.

### Worker

The `worker` container runs two processes:

- `manage.py rundramatiq --reload --reload-use-polling --processes 1 --threads 2`, which
  executes Dramatiq actors and restarts when a `.py` file under `src/` changes (`src/` is
  bind-mounted).
- `manage.py crontab` (dramatiq-crontab), which enqueues actors decorated with `@cron(...)` /
  `@interval(...)`. A Redis lock keeps it to one scheduler.

If either process exits, the container exits and `restart: unless-stopped` brings both back.
`docker compose logs -f worker` shows a heartbeat line every minute when both are healthy. The
heartbeat is dramatiq-crontab's own actor; the project has no actors yet.

Put actors in `<app>/tasks.py`; both commands discover them automatically. After changing
dependencies, rebuild with `docker compose up -d --build worker`.

All configuration comes from `.env` (see `.env.example`). Docker Compose reads the same file, so
the Postgres credentials and ports are defined once.

## Tests

```sh
docker compose up -d --wait   # the health tests use the real Postgres and Redis
uv run pytest -q
```

pytest-django creates a separate test database, so the tests never touch your data.

## Layout

```
src/
  manage.py
  config/      Django project: settings, urls, wsgi/asgi
  sync/        the sync app (no models yet)
  utils/       shared bits: /health/ endpoint, rundramatiq override (fixes --reload), tests
docs/          Round1.html (the brief), design.md (design doc), chats/ (AI chat exports)
wiki/          hand-written GitHub wiki pages; CI builds and publishes them
scripts/       build-wiki.sh (wiki build), export-chats.py (AI chat export)
.github/       workflows/wiki.yml: publishes the wiki on push to main
.claude/       Claude Code agents and commands (the AI workflow source of truth)
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
