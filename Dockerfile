# Image for the dramatiq worker (+ cron scheduler). Django's web server still runs on the host.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /bin/uv

# venv lives outside /app/src so the dev bind mount of ./src doesn't hide it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

COPY src ./src
WORKDIR /app/src
