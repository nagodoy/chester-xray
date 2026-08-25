#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")"

python -m chester.schema

python -m chester.worker &
worker_pid=$!

python -m uvicorn chester.main:app --host 0.0.0.0 --port "${PORT:-5000}" &
api_pid=$!

stop_children() {
  trap - EXIT INT TERM
  kill -TERM "$worker_pid" "$api_pid" 2>/dev/null || true
  wait "$worker_pid" 2>/dev/null || true
  wait "$api_pid" 2>/dev/null || true
}

trap stop_children EXIT INT TERM

set +e
wait -n "$worker_pid" "$api_pid"
exit_code=$?
set -e

stop_children
exit "$exit_code"