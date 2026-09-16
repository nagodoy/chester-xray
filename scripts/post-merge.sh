#!/usr/bin/env bash
set -Eeuo pipefail

npm --prefix web ci --no-audit --no-fund
python -m pip install --disable-pip-version-check -e "server[dev,gateway]"

(
  cd server
  python -m chester.schema
)