#!/usr/bin/env bash
set -euo pipefail

echo "=========================================================="
echo "sync-data: Round 1 End-to-End Demo (Phase 8)"
echo "Brief checks at rate cap SALESFORCE_RATE_LIMIT=5"
echo "=========================================================="

# 1. Start compose and migrate
docker compose up -d --wait db redis
uv run python src/manage.py migrate

# 2. Run scenario tests marked brief
echo ""
echo "Running the brief's required checks..."
SALESFORCE_RATE_LIMIT=5 uv run pytest -q -m brief

echo ""
echo "=========================================================="
echo "All brief checks passed under rate limit!"
echo "=========================================================="
