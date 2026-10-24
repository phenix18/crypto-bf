#!/usr/bin/env bash
# Prepare a local development environment: Python venv, node modules, .env.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv}"

echo "==> Python virtualenv ($VENV)"
if [ ! -d "$VENV" ]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -r service/requirements.txt

if [ "${WITH_RESEARCH:-0}" = "1" ]; then
  echo "==> Research extras (pandas, scipy, statsmodels, networkx, matplotlib)"
  "$VENV/bin/pip" install -r research/requirements.txt
fi

echo "==> Frontend dependencies"
if command -v npm >/dev/null 2>&1; then
  (cd service/frontend && npm ci)
else
  echo "    npm not found — skipping frontend install"
fi

echo "==> Environment file"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    created .env from .env.example"
else
  echo "    .env already exists — left untouched"
fi

mkdir -p service/server/data

cat <<'MSG'

Bootstrap complete.

  make test        run the backend test suite
  make api         start the API on http://127.0.0.1:8000
  make worker      start the background worker (prices, settlements, market intel)
  make frontend    start the Vite dev server on http://127.0.0.1:3000
  make smoke       exercise a live instance end to end

MSG
