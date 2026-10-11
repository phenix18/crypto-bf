# Deploying the SPA on Vercel

Vercel can host the React front end. It cannot host the backend: AI-Trader needs
a long-running Python process plus a background worker, which is what
`docker-compose.yml` is for. Read this before wiring the two together.

## Node version

Vercel discontinued Node 20 on 2026-10-01; every project still pinned to it
fails during the clone step, before any build command runs:

```
Node.js Version "20.x" is discontinued and must be upgraded.
Please set Node.js Version to 24.x in your Project Settings to use Node.js 24.
```

`package.json` and `service/frontend/package.json` now declare:

```json
"engines": { "node": "24.x" }
```

Vercel reads `engines.node` from the repository, so a fresh deployment picks up
Node 24 without touching the dashboard. If a project still fails, its **Project
Settings → Node.js Version** holds an explicit override that wins over
`engines`; set it to 24.x there as well.

## What gets built

`vercel.json` builds the SPA and serves it with a client-side routing fallback:

| Setting | Value |
|---|---|
| Build | `npm ci --prefix service/frontend && npm run build --prefix service/frontend` |
| Output | `service/frontend/dist` |
| Rewrites | everything except `/api/`, `/assets/`, `/SKILL.md` and `/skill/` falls back to `index.html` |

Without this file Vercel finds no build script at the repository root and
publishes an empty site.

## Pointing the SPA at a backend

The SPA calls the API through relative `/api` paths. On Vercel those paths have
nothing behind them, so a deployment renders the interface and every request
fails. Two ways to fix that:

**Same origin (simplest).** Don't use Vercel. `make build` puts the SPA in
`service/frontend/dist` and the FastAPI process serves it at `/` — one origin,
no CORS, no rewrites. This is what the Docker image does.

**Split origins.** Keep Vercel for the SPA and add a rewrite to your deployed
backend:

```json
{ "source": "/api/:path*", "destination": "https://your-backend.example.com/api/:path*" }
```

Add that host to `CLAWTRADER_CORS_ORIGINS` on the backend, and remember the
rewrite destination is baked into `vercel.json` at deploy time — Vercel does not
interpolate environment variables there.
