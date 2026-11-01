#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")"
export CHESTER_PROCESS_ROLE=worker

exec python -m chester.worker "$@"
