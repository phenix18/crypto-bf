#!/usr/bin/env bash
# Run the whole stack locally: FastAPI, the background worker, and the Vite dev
# server. Ctrl-C stops all three.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="${VENV:-.venv}"
PY="$ROOT/$VENV/bin/python"
API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${VITE_DEV_PORT:-3000}"

[ -x "$PY" ] || { echo "No virtualenv at $VENV — run scripts/bootstrap.sh first." >&2; exit 1; }
[ -f .env ] || cp .env.example .env
mkdir -p service/server/data

pids=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${pids[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "==> API        http://127.0.0.1:$API_PORT"
(cd service/server && API_STDERR_LOG=true exec "$PY" -m uvicorn main:app --host 0.0.0.0 --port "$API_PORT" --reload) &
pids+=($!)

if [ "${WITH_WORKER:-1}" = "1" ]; then
  echo "==> Worker     background jobs (prices, settlements, market intel)"
  (cd service/server && exec "$PY" worker.py) &
  pids+=($!)
fi

if [ "${WITH_FRONTEND:-1}" = "1" ] && command -v npm >/dev/null 2>&1; then
  echo "==> Frontend   http://127.0.0.1:$FRONTEND_PORT (proxying /api to the API)"
  (cd service/frontend && VITE_API_PROXY_TARGET="http://127.0.0.1:$API_PORT" exec npm run dev -- --port "$FRONTEND_PORT") &
  pids+=($!)
fi

wait -n
echo "A process exited — shutting the stack down." >&2
