#!/usr/bin/env bash
set -Eeuo pipefail

# An external worker must never silently fall back to the local SQLite database.
# Do not log values: DATABASE_URL and the application secrets are credentials.
for key in DATABASE_URL SESSION_SECRET PSEUDONYM_SECRET DICOM_INGEST_TOKEN; do
  if [[ -z "${!key:-}" ]]; then
    printf 'Worker startup refused: configure %s in Railway Variables.\n' "$key" >&2
    exit 1
  fi
done
case "$DATABASE_URL" in
  postgresql://*|postgresql+psycopg://*) ;;
  *)
    printf 'Worker startup refused: DATABASE_URL must point to the production PostgreSQL database.\n' >&2
    exit 1
    ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export MODEL_PATH="${MODEL_PATH:-$ROOT/models/chester-all-224.onnx}"
export SEGMENTATION_MODEL_PATH="${SEGMENTATION_MODEL_PATH:-$ROOT/models/chest-segmentation-512-int8.onnx}"
for path in "$MODEL_PATH" "$SEGMENTATION_MODEL_PATH"; do
  if [[ ! -s "$path" ]]; then
    printf 'Worker startup refused: a required ONNX model is missing from the build.\n' >&2
    exit 1
  fi
done

exec bash "$ROOT/server/run_worker.sh" "$@"
