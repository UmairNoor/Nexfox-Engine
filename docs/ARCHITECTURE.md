# Architecture

## Components

| Component | Tech | Runs on | Responsibility |
|---|---|---|---|
| Web | Next.js 15 (standalone) | Linux, behind nginx | Public site, customer dashboard, `/admin` console. No business logic: everything is enforced by the API |
| API | FastAPI + SQLAlchemy 2 | Linux (uvicorn, 2 workers) | Auth, tenants, billing, entitlements, bots, instances, admin, EA gateway, worker gateway, websocket |
| Scheduler | Python process | Linux | Watchdogs, lease expiry, stale order intents → uncertain, trial expiry + notices, lapse policy, payment grace, retention. DB leader lease: one active runner |
| PostgreSQL 16 | | Linux | System of record. JSONB for documents, RLS as defence in depth |
| Redis | optional | Linux | Websocket event fan-out across API processes |
| Worker | Python supervisor | Windows (MT5 needs Windows) | Leases worker-hosted bot instances, one OS process + one MT5 terminal per instance |
| EA | MQL5 | Customer's MT5 | AurumScalp; talks to `/ea/v1` with HMAC-signed responses |

## Module boundaries (API)

```
routers/      HTTP only: parse, authorize (deps), call services, shape responses
  auth, billing, accounts, bots, data, admin, ea_gateway, worker_gateway
services/     business rules, no HTTP
  entitlements   plan/trial/subscription checks for every restricted operation
  instances      bot instance lifecycle (assign, config versions, start/pause/stop/close_all, live authorization)
  trading_guard  order intents: fencing, idempotency, risk limits, uncertain-order blocking, result transitions
  payments       provider adapters (stripe / manual / mock) -> normalised billing events
  billing        subscription + license state machine (idempotent events)
  agents         AI agent contract + runtime (scopes, limits, labelled outputs)
  trades         trade event ingestion (shared by EA + worker gateways)
  platform, locks, notify, mailer, risk, stats, catalog, bus
bots/         integration contract (base.py), adapters, registry (explicit, code-reviewed)
models.py     tables; migrations/ is the only way the schema changes
observability structured logs, correlation ids, security headers;  errors.py  error categories
```

## Data model (main tables)

* Identity & tenancy: `tenants`, `users` (role, status, token_version), `auth_sessions` (refresh rotation), `email_codes`
* Billing: `plans` (features JSONB = entitlements), `subscriptions`, `invoices`, `billing_events` (idempotency), `licenses`
* Accounts: `broker_accounts` (login/server/type, encrypted MT5 password, verified connection status), `account_configs` (EA presets)
* Bots: `bots`, `bot_versions` (manifest + config_schema + default_config JSONB, status draft/approved/halted/retired),
  `bot_instances` (one active per account — partial unique index), `bot_config_history`, `bot_runs` (version + config
  snapshot + runtime versions per execution), `bot_events`, `order_intents`, `positions`, `commands`
* Workers: `workers` (token hash, capabilities), `worker_jobs` (verify_account, self_test)
* AI: `ai_agents`, `ai_agent_versions` (manifest JSONB), `ai_tasks` (input/output JSONB, labelled generated)
* Platform: `platform_settings` (halt, trial policy), `scheduler_locks`, `audit_log`, `notifications`, time series
  `ea_heartbeats`, `candles`

Relational facts (who owns what, states, money, identifiers) are typed columns with foreign keys and constraints.
JSONB holds documents that genuinely vary per bot/agent (config, manifests, schemas, event payloads, task I/O) and
every such document is validated before storage (`bots/base.py validate_against_schema`, `agents.validate_manifest`,
pydantic models) and carries a `schema_version`/`contract_version`.

## Instance lifecycle

```
            start/resume (entitlement, halt, credentials, live gate checked)
 stopped ──────────────────────────────> pending ──claim──> starting ──> running <──> paused
    ▲                                                                   │   │
    └──────────── stop (process ends, positions untouched) ─────────────┘   └─ failed (5 in a row → stopped)
 offline: worker stopped reporting → lease expires → another worker re-claims (new lease epoch, new run)
```

## Order path for worker-hosted bots

1. Strategy calls `trade_engine.open_trade` (unchanged strategy code).
2. `ExecutionGuard.before_order`: local checks (paused, halt, entitlement, unresolved orders, symbol, lot cap, SL).
3. `POST /worker/v1/instances/{id}/intents` with a new `client_order_id`, the lease epoch and the run id. The server
   checks lease/fencing, desired state, halt, entitlement, account (demo/live + authorization), lot cap, SL, symbol,
   unresolved intents, daily cap → `approved` (idempotent on retry of the same id).
4. `order_send` with the id embedded in the MT5 comment (`TP1_LQ_H1_buy#<id>`, ≤31 chars).
5. Result → `filled` / `rejected` / `uncertain` (None from `order_send`). Uncertain is reconciled against positions and
   deal history → `reconciled_filled` / `reconciled_absent`. Until then the server refuses new intents. Results that
   cannot be reported are journalled on disk and replayed. Approved intents with no result for 2 minutes are marked
   uncertain by the scheduler (worker died mid-send).

## Tenancy and authorization

Every customer query filters by `tenant_id` in code; PostgreSQL RLS (`infra/sql/010_rls.sql`, applied per transaction
via `app/db.py`) is a second layer for customer sessions. Admin endpoints are role-gated (`staff_user` read,
`admin_user` write, both require TOTP) and audited. Workers authenticate with per-host tokens and can only act on
instances they currently lease. The EA authenticates with license key + bound account.
