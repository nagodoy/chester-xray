#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")"

python -m chester.schema

exec python -m uvicorn chester.main:app --host 0.0.0.0 --port "${PORT:-5000}"
