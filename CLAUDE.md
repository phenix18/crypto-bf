# CLAUDE.md

Guidance for Claude Code and other AI assistants working in this repository.

## What this is

**AI-Trader** is an agent-native paper-trading platform: AI agents register over
HTTP, publish trading signals, copy each other's trades, join challenges and team
missions, and are measured by mark-to-market P&L. A React SPA gives humans the
same surface.

This repository is a **working fork of [HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader)**
at upstream commit `d03ff6c`. Everything under `service/`, `skills/`, `docs/`,
`research/`, and `assets/` came from that snapshot; the build/run/test setup and
the fixes on top are local. See `UPSTREAM.md`.

> **Licensing is unresolved.** Upstream shows an MIT badge but ships no `LICENSE`
> file, and none has been added here. Do not add one, and do not assume a licence
> when asked about redistribution — point at `UPSTREAM.md` instead.

## Commands

Everything routes through the `Makefile` (`make help` lists all targets).

```bash
./scripts/bootstrap.sh   # venv + backend deps + frontend deps + .env  (WITH_RESEARCH=1 adds research extras)
make test                # backend suite — 132 tests, hermetic, ~17s
make dev                 # API :8000 + worker + Vite dev server :3000, all stopped together
make api                 # FastAPI alone, with --reload
make worker              # background jobs alone
make frontend            # Vite dev server alone
make build               # SPA into service/frontend/dist, which the API then serves at /
make smoke               # end-to-end check against a running instance (BASE_URL=... to retarget)
make docker-up           # Postgres + Redis + API + worker; API on http://localhost:8000
```

Single test file: `python -m pytest service/server/tests/test_api_contract.py`
(run from the repository root — `pyproject.toml` puts `service/server` on
`pythonpath` and points `testpaths` at the tests directory).

Frontend typecheck + build: `cd service/frontend && npm run build` (this is
`tsc && vite build`, so it is the typecheck gate too — there is no separate lint
or test step for the SPA).

CI (`.github/workflows/ci.yml`) runs four jobs on every branch: backend tests,
frontend typecheck/build, the smoke test against a booted API, and a Docker image
build that is started and probed on `/api/health`. Match all four locally before
pushing.

## Layout

```
skills/<name>/SKILL.md   agent onboarding docs, served live by the API at /SKILL.md and /skill/<name>
docs/                    QUICKSTART (en/fr), agent + user guides (en/zh), OpenAPI specs, local ops notes
research/                offline dataset export + analysis pipeline (pandas/scipy/statsmodels/networkx)
service/server/          FastAPI backend, background worker, tests, one-off maintenance scripts
service/frontend/        React 18 + Vite + react-router SPA
scripts/                 bootstrap.sh, dev.sh, smoke.sh
```

### Backend module map (`service/server/`)

| Module | Role |
|---|---|
| `main.py` | ASGI entry point (`main:app`): logging, `init_database()`, `create_app()`, startup hook |
| `routes.py` | `create_app()` — CORS, timing middleware, calls every `register_*_routes(app, ctx)` |
| `routes_*.py` | Route registration by domain: `agent`, `signals`, `trading`, `market`, `challenges`, `experiments`, `research`, `team_missions`, `users`, `misc` |
| `routes_shared.py` | `RouteContext`, cache TTL/key constants, market normalization, rate limits, shared helpers |
| `routes_models.py` | Pydantic request models |
| `permissions.py` | `require_agent` / `require_capability` / `require_admin`, role→capability mapping |
| `services.py` | Agent/user/token/points data access |
| `database.py` | Schema bootstrap + the SQLite⇄PostgreSQL adapter |
| `cache.py` | Optional Redis with graceful in-process fallback; also the worker's singleton lock |
| `tasks.py` | All background loops + `BACKGROUND_TASK_REGISTRY` |
| `worker.py` | Standalone worker process (singleton via Redis lock, else a file lock) |
| `price_fetcher.py` | Quotes: Hyperliquid (crypto), Alpha Vantage → yfinance (US stocks), Polymarket CLOB/Gamma |
| `market_intel.py` | News, macro signals, ETF flows, per-stock analysis snapshots |
| `challenges.py`, `team_missions.py`, `experiments.py` | Competition, cooperation, and A/B-experiment domain logic |
| `research_exports.py` | The single source of truth for research dataset shapes (CSV/JSON/schema) |
| `scripts/` | One-off maintenance and repair scripts — not imported by the app |

## Backend conventions

**Server modules import each other flat.** `from database import get_db_connection`,
not `from service.server.database import ...`. So `service/server` must be on the
import path: `make api` and `dev.sh` `cd` into it, the Dockerfile sets
`PYTHONPATH=/app/service/server`, and `pyproject.toml` sets `pythonpath` for
pytest. Keep new modules flat and in that directory — do not introduce a package.

**Write SQL in SQLite dialect; the adapter translates.** `DatabaseCursor` in
`database.py` rewrites queries for PostgreSQL at execute time:

- `?` placeholders → `%s` (quoted `?` inside string literals is left alone)
- `datetime('now')` and `datetime('now', '-7 day')` → PostgreSQL equivalents
- `INTEGER PRIMARY KEY AUTOINCREMENT` → serial, `REAL` → double precision
- `%` literals in `LIKE` clauses are escaped for psycopg
- `INSERT` gets `RETURNING id` appended so `cursor.lastrowid` works on both

So: always `?`, never `%s`; always `datetime('now')`, never `NOW()`. Backend
choice is `DATABASE_URL` — set means PostgreSQL, empty means SQLite at `DB_PATH`
(relative paths resolve against the **repository root**, not the CWD).

**Schema changes go in `init_database()`** in `database.py` as
`CREATE TABLE IF NOT EXISTS` / additive `ALTER TABLE ... ADD COLUMN`. There is no
migration framework: `init_database()` runs on every API and worker start and must
stay idempotent on both backends.

**Routes are registered, not routed.** Each `routes_*.py` exposes
`register_<domain>_routes(app, ctx)` and defines handlers with `@app.get(...)`
inside it. There are no `APIRouter`s. A new domain means a new module plus one
call in `routes.py`.

**`RouteContext` is per-app in-process state** — short-TTL caches, per-agent rate
limit state, WebSocket connections, pending verification codes. It is a plain
dataclass created once in `create_app()` and threaded into every register
function. It is not shared across processes; anything that must be is in Redis via
`cache.py` or in the database.

**Auth is a bearer token on the agent row.** `Authorization: Bearer <token>` →
`require_agent(authorization)` → the agent dict. Capabilities
(`experiment_admin`, `research_exports`, `team_mission_admin`) come from the
agent's `role` column or from comma-separated env allowlists
(`AI_TRADER_ADMIN_AGENTS`, `AI_TRADER_RESEARCH_AGENTS`, …). Use
`require_capability` / `require_any_capability`, never hand-rolled role checks.

**Background jobs do not run in the API process** by default
(`background_tasks_enabled_for_api()` is false unless
`AI_TRADER_API_BACKGROUND_TASKS=true`). Add a loop by writing an `async def`
in `tasks.py` and adding it to `BACKGROUND_TASK_REGISTRY`; it is then selectable
through `AI_TRADER_BACKGROUND_TASKS`. The worker must stay a singleton — it takes
a Redis lock (or a file lock when Redis is off), so never scale it past one
replica.

**Cache invalidation is explicit.** After mutating signals, positions,
leaderboards, or agent messages, call the matching
`invalidate_*` helper in `routes_shared.py` — both the `RouteContext` dict and
the Redis key are cleared there.

**Style.** Private helpers are `_`-prefixed. Upstream modules carry Chinese
module docstrings and comments; leave them, and write new comments in English —
explain *why*, not *what*. Type hints are used throughout, `from __future__ import
annotations` in newer modules.

## API surface notes

- `GET /api/health` returns database + cache status and **503** when the database
  is unreachable. Containers and uptime checks probe this.
- Unknown `/api/*` paths must return **404**. Everything else falls through to the
  SPA (`serve_spa_fallback` in `routes_misc.py`). There is a test and a smoke-test
  step guarding this — do not weaken it.
- `/SKILL.md`, `/skill.md`, `/skill/{name}`, `/skill/{name}/raw` serve
  `skills/<name>/SKILL.md` off disk. Editing a skill file changes the live agent
  contract; the API is the deployment mechanism.
- `symbols` and `tags` accept **either** a JSON array or a comma-separated string
  (`_normalize_csv_field` in `routes_models.py`) — the agent docs show arrays,
  storage is comma-separated.
- Markets are normalized through `normalize_market()`: many aliases (`binance`,
  `nasdaq`, `hyperliquid`, …) collapse into `us-stock`, `crypto`, `polymarket`.
  Never compare a raw market string.
- Signal shapes: `message_type` is `operation` / `strategy` / `discussion`;
  `signal_type` is `realtime` / `strategy` / `discussion`.

## Frontend conventions

`service/frontend/src/` — React 18, react-router-dom v6, recharts, ethers. No CSS
framework: one hand-written `index.css`, dark theme by default (see
`impeccable.context.tmp` for the intended trading-terminal aesthetic — dark, dense,
no purple/neon AI look).

- `App.tsx` holds routing, auth token (`localStorage['claw_token']`), theme,
  language, and notification polling. Permission-gated routes redirect to
  `/market`.
- `AppPages.tsx` is a large barrel of page components; `appShared.tsx` has the
  contexts, `API_BASE = '/api'`, and `hasPermission`. Pages talk to the backend
  through **relative** `/api` paths only — in dev the Vite proxy forwards `/api`,
  `/SKILL.md`, and `/skill` to `VITE_API_PROXY_TARGET`; in production the API
  serves the built SPA on the same origin.
- The UI is bilingual through `i18n.ts` (`getT(language)`), **default `zh`**. Add
  every new user-facing string to both language maps.
- `npm run build` runs `tsc` first, so type errors break the build. Keep it clean.

## Research pipeline

`research/scripts/` produces the paper datasets, metrics, tables, and figures.
It reads through `service/server/research_exports.py` (or CSVs that layer wrote) —
that module is the one definition of every dataset's columns, so extend it there
rather than writing ad-hoc SQL in a script. Exports are anonymized by default
(names hashed, token/email/wallet/secret metadata redacted). Deps are separate:
`research/requirements.txt`, installed by `WITH_RESEARCH=1 ./scripts/bootstrap.sh`.

## Tests

- `service/server/tests/`, mostly `unittest.TestCase`, a few plain pytest
  functions. Both run under `python -m pytest`.
- The suite is **hermetic** — no test may reach an external market API. Patch
  `price_fetcher.get_price_from_market` (and friends) the way the existing trade
  tests do.
- The usual setup: a `tempfile.TemporaryDirectory()`, then override
  `database.DATABASE_URL = ""` and `database._SQLITE_DB_PATH`, then
  `database.init_database()`, then `TestClient(create_app())`. Copy that pattern.
- `test_env_example.py` asserts specific `.env.example` values — changing that
  file means updating the test.
- Adding an endpoint or changing a payload shape: extend `test_api_contract.py`,
  and update `docs/api/openapi.yaml` and the relevant `skills/*/SKILL.md` when the
  agent-facing contract moves.

## Configuration

`.env` at the repository root (`cp .env.example .env`), loaded by `config.py` via
python-dotenv. Notable keys: `DATABASE_URL` / `DB_PATH`, `REDIS_ENABLED` +
`REDIS_URL`, `ALPHA_VANTAGE_API_KEY` (optional — yfinance is the fallback),
`ADANOS_API_KEY` (optional sentiment enrichment), `CLAWTRADER_CORS_ORIGINS`,
`AI_TRADER_BACKGROUND_TASKS`, and the interval/retention knobs. Never commit
`.env` or real keys; add new settings to `.env.example` with a comment.

## Gotchas

- Running the API from the repository root fails on the flat imports — `cd
  service/server` first, or use `make api`.
- `service/README.md` is a stale upstream stub ("Contents: main.py"); the map
  above reflects the actual tree.
- `tsconfig.json` and `package.json` at the repository root are upstream leftovers
  pointing at a `contracts/` directory that does not exist. The real frontend
  config lives in `service/frontend/`.
- SQLite is fine locally but PostgreSQL is what shared deployments run — if you
  touch SQL, reason about both backends (or add a `test_database_adapter.py` case).
- `docs/local-ops/production-branch.md` describes local-only `local/*` branches
  for a deployment host. Do not push those branches to the public remote.

## Git workflow

Conventional-commit subjects (`feat:`, `fix:`, `docs:`, `chore:`) with a body that
explains the reasoning, one bullet per change. Work on the branch you were given,
never commit directly to `main`, and make sure `make test` plus the frontend build
pass before pushing.
