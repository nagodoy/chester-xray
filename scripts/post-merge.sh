#!/usr/bin/env bash
set -euo pipefail

if [[ -f package-lock.json ]]; then
  npm ci --ignore-scripts --no-audit
elif [[ -f package.json ]]; then
  npm install --ignore-scripts --no-audit
fi