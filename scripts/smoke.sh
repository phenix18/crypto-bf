#!/usr/bin/env bash
# End-to-end smoke test against a running AI-Trader instance.
#
#   scripts/smoke.sh [base_url]        # default http://127.0.0.1:8000
#
# Registers a throwaway agent, publishes a strategy, reads it back from the
# feed, and checks the leaderboard. Exits non-zero on the first failure.
set -euo pipefail

BASE="${1:-${AI_TRADER_BASE_URL:-http://127.0.0.1:8000}}"
SUFFIX="$(date +%s)-$$"
AGENT="smoke-${SUFFIX}"
PASSWORD="Sm0ke-${SUFFIX}!"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

# Read one top-level key out of a JSON document on stdin.
field() { python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"; }

say "1/6 health"
HEALTH="$(curl -fsS "$BASE/api/health")" || fail "health endpoint unreachable at $BASE"
echo "$HEALTH"
[ "$(printf '%s' "$HEALTH" | field status)" = "ok" ] || fail "health status is not ok"

say "2/6 unknown API paths must 404"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/__no_such_route__")"
[ "$CODE" = "404" ] || fail "expected 404 for unknown API path, got $CODE"

say "3/6 register agent ($AGENT)"
REG="$(curl -fsS -X POST "$BASE/api/claw/agents/selfRegister" \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"$AGENT\",\"password\":\"$PASSWORD\",\"email\":\"$AGENT@example.com\"}")" \
  || fail "registration failed"
TOKEN="$(printf '%s' "$REG" | field token)"
[ -n "$TOKEN" ] || fail "no token returned"
echo "token acquired (${TOKEN:0:8}...)"

say "4/6 authenticated identity"
ME="$(curl -fsS "$BASE/api/claw/agents/me" -H "Authorization: Bearer $TOKEN")" || fail "/me failed"
[ "$(printf '%s' "$ME" | field name)" = "$AGENT" ] || fail "/me returned the wrong agent"
echo "cash: $(printf '%s' "$ME" | field cash)"

say "5/6 publish a strategy with the documented list payload"
curl -fsS -X POST "$BASE/api/signals/strategy" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"market":"crypto","title":"Smoke strategy","content":"Automated smoke test strategy payload.","symbols":["BTC","ETH"],"tags":["smoke"]}' \
  >/dev/null || fail "strategy publish rejected"
FEED="$(curl -fsS "$BASE/api/signals/feed?limit=5")"
printf '%s' "$FEED" | grep -q '"BTC"' || fail "published strategy not visible in the feed"
echo "strategy visible in feed"

say "6/6 leaderboard"
curl -fsS "$BASE/api/leaderboard/position-pnl?limit=3" >/dev/null || fail "leaderboard failed"
echo "leaderboard responded"

printf '\n\033[32mSmoke test passed against %s\033[0m\n' "$BASE"
