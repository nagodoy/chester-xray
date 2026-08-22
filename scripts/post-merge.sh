#!/usr/bin/env bash
set -euo pipefail

if [[ -f package-lock.json ]]; then
  npm ci --ignore-scripts --no-audit
elif [[ -f package.json ]]; then
  npm install --ignore-scripts --no-audit
fi

# Apply schema to development PostgreSQL database
if [[ -n "${DATABASE_URL:-}" && "${DATABASE_URL}" == postgres* ]]; then
  echo "Applying db/schema.sql to development database..."
  psql "$DATABASE_URL" -f db/schema.sql || echo "Schema apply failed (may already exist)"
fi
