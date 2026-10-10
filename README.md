# NexFox Engine

A multi-tenant SaaS platform for running automated MetaTrader 5 trading bots with server-side risk controls,
subscriptions, a customer dashboard and a super-admin console. **Rehman Bot** (XAUUSD liquidity sweep strategy) is the
first worker-hosted bot; the **AurumScalp EA** (MQL5) is the first terminal-hosted bot. New bots and AI agents plug in
through documented contracts.

> Trading can lose money. Live (real-money) trading is **off by default** and needs a platform switch, a paid plan and
> the account owner's explicit authorization per bot. Nothing in this repository promises performance.

## Repository layout

```
apps/
  api/        FastAPI backend: auth, tenants, billing, bots, instances, admin, EA + worker gateways, scheduler
    app/bots/       bot registry + integration contract (adapters: rehman.py, aurumscalp.py)
    app/services/   entitlements, instances (lifecycle), trading_guard (order intents), payments, agents, ...
    migrations/     Alembic migrations (0001 baseline, 0002 multi-bot platform)
    tests/          pytest: API, platform, migrations
  web/        Next.js 15 dashboard (customer app + /admin console), Vitest tests
  worker/     Windows MT5 worker: supervisor, one process per bot instance, execution guard, simulated MT5
  ea/         AurumScalp.mq5 Expert Advisor
  prototype/  original clickable HTML prototype (reference only)
bots/
  rehman_bot/ Rehman Bot strategy (original code, unchanged logic; credentials now from env/vault)
deploy/
  linux/      install.sh, deploy.sh (release + auto-rollback), rollback.sh, backup/restore, systemd units, nginx
  windows/    install-worker.ps1, run-worker.ps1 (supervision), dev-start.ps1 (local dev)
infra/sql/    010_rls.sql (PostgreSQL row-level security), 020_timescale_optional.sql
docs/         ARCHITECTURE, BOT_INTEGRATION, AI_AGENTS, DEPLOYMENT, OPERATIONS, SECURITY, TESTING
```

## How it works (short)

```
 Browser ──HTTPS──> nginx ──> Next.js (dashboard)            Windows MT5 host(s)
                       └────> FastAPI ──> PostgreSQL          ┌───────────────────────────────┐
                                 ▲  ▲                         │ nexfox_worker supervisor       │
              EA in customer's   │  └──── /worker/v1 ─HTTPS───│  ├─ instance process ─ MT5 #1  │
              MT5 ── /ea/v1 ─────┘   (lease + fencing token,  │  └─ instance process ─ MT5 #2  │
                                      order intents)          └───────────────────────────────┘
                     scheduler (separate process): watchdogs, trial expiry, order timeouts, retention
```

* **Bots** are registered adapters with versioned manifests and config schemas (`docs/BOT_INTEGRATION.md`).
* **Instances** bind one bot to one MT5 account. `desired_state` is what people ask for; `state` is what the runtime reports.
* **Workers** claim instances with a lease; every claim bumps a fencing token so a stale worker can never trade.
* **Every order** from a worker-hosted bot needs a server-approved *order intent* first (plan, halt, risk limits,
  live authorization, stop loss, no unresolved orders). Unknown outcomes are reconciled, never blindly retried.
* **Stop ≠ close**: stopping a bot never closes positions; *close all* is a separate, confirmed operation.

## Local development (Windows, no Docker)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\dev-start.ps1
```
Starts the API (SQLite, mock billing, migrations applied), the web app and a worker on the **simulated** MT5 backend.
Sign up at http://localhost:3000, the verification code is printed in the API window. Simulated MT5 account:
login `1000001`, server `Fake-Demo`, password `demo-pass`. For an admin:
`$env:ADMIN_PASSWORD='long-password-here'; .\.venv\Scripts\python.exe -m app.cli create-admin you@example.com "You"` in `apps/api`.

Manual steps instead (PowerShell, each block in its own window, starting from the repo root):
```powershell
cd apps\api; py -3 -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn.exe app.main:app --reload --port 8000     # API docs: http://localhost:8000/docs
```
```powershell
cd apps\web; npm ci; npm run dev                                   # http://localhost:3000
```
```powershell
cd apps\worker; py -3 -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
$env:MT5_BACKEND='fake'; $env:WORKER_TOKEN='...'; .\.venv\Scripts\python.exe -m nexfox_worker
```

## Commands

| Where | Command | What |
|---|---|---|
| apps/api | `python -m pytest -q` | API, platform and migration tests |
| apps/api | `python -m app.cli migrate` | apply Alembic migrations (stamps a legacy `create_all` DB at 0001 first) |
| apps/api | `python -m app.cli check` | production config + schema-at-head check (non-zero on problems) |
| apps/api | `python -m app.cli seed` | upsert plans, presets, bots, AI agents |
| apps/api | `python -m app.cli gen-key` | new `MT5_CREDENTIALS_KEY` |
| apps/api | `ADMIN_PASSWORD=... python -m app.cli create-admin EMAIL NAME` | super-admin with TOTP |
| apps/api | `python -m app.cli create-worker NAME [SLOTS]` | register a worker host, prints its token once |
| apps/api | `python -m app.scheduler` | periodic jobs (separate service in production) |
| apps/worker | `python -m pytest -q` | worker unit tests + full end-to-end (starts a real API server) |
| apps/worker | `python -m nexfox_worker --check` | validate worker config and API connectivity |
| apps/web | `npm run typecheck && npm test && npm run build` | type check, Vitest, production build |

## Configuration

API settings come from environment variables (`apps/api/app/config.py`; templates in `.env.example` and
`deploy/linux/env/api.env.example`). The most important:

| Variable | Purpose |
|---|---|
| `ENV` | `dev` / `test` / `prod`. In `prod` the API refuses to start with unsafe settings |
| `DATABASE_URL` | PostgreSQL in production (`postgresql+psycopg://...`); SQLite only for tests |
| `APP_SECRET`, `EA_SIGNING_SECRET`, `MT5_CREDENTIALS_KEY` | JWT/HMAC secrets and the MT5 password encryption key |
| `BILLING_PROVIDER` | `stripe`, `manual` (admins grant plans) or `mock` (dev only, refused in prod) |
| `ALLOW_LIVE_TRADING` | platform gate for real-money orders (default `false`) |
| `MAX_LOT_HARD`, `MAX_ORDERS_PER_INSTANCE_DAY` | platform-wide order caps |
| `ENTITLEMENT_LAPSE_POLICY` | `block_new_entries` (default) or `pause` when a plan lapses |
| `REDIS_URL` | websocket fan-out across API processes |
| `SMTP_*` | email delivery for verification and password reset |

Worker settings: `apps/worker/.env.example`. Web build settings: `deploy/linux/env/web.env.example`.

## Deployment

No Docker. Linux server (Ubuntu 24.04) for web/API/scheduler/PostgreSQL/nginx and a Windows host for MT5 workers:
see **docs/DEPLOYMENT.md**. Operations, monitoring, backups and incident runbooks: **docs/OPERATIONS.md**.

## Adding a bot or AI agent

See **docs/BOT_INTEGRATION.md** and **docs/AI_AGENTS.md**. No code is ever uploaded or loaded dynamically.
