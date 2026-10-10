# Testing and verification record

What was actually run, with the actual results. Last full run: **2026-10-10**, Windows 11, Python 3.14, Node 22.
Anything not listed under "Verified" was **not** verified (see the last section).

## How to run

| Where | Command | Covers |
|---|---|---|
| `apps/api` | `.\.venv\Scripts\python.exe -m pytest -q` | API, auth, billing, multi-bot platform, worker gateway, order-intent safety, admin, migrations, security regressions |
| `apps/worker` | `.\.venv\Scripts\python.exe -m pytest -q` | MT5 connection checks, execution guard, Rehman Bot strategy on the simulated terminal, lease self-fencing, **end-to-end** (real API server + real supervisor) |
| `apps/web` | `npm run typecheck`, `npm test`, `npm run build` | TypeScript, components/API client (Vitest), production build |
| `apps/api` | `.\.venv\Scripts\python.exe -m alembic upgrade head` / `downgrade 0002` / `check` | migrations up/down, models match the schema |
| `apps/api` | `.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt` (and `-r ..\worker\requirements.txt`) | known vulnerabilities in Python dependencies |
| `apps/web` | `npm audit` | known vulnerabilities in npm dependencies |

The test suites use throw-away **SQLite** databases. The PostgreSQL schema is checked by rendering the migrations to
SQL offline (`alembic upgrade --sql` with a PostgreSQL URL; no connection is made).

## Results (2026-10-10, after the security / trading-safety fixes)

| Check | Result |
|---|---|
| API tests | **58 passed** (46 existing + 12 new regression tests in `tests/test_security_fixes.py`) |
| Worker tests | **22 passed** in 28.8 s (incl. 2 end-to-end tests and 7 new safety tests) |
| Web typecheck | clean (`tsc --noEmit`, exit 0) |
| Web tests | **12 passed** (2 files) |
| Web build | compiled; **26/26** static pages generated |
| Migrations (SQLite) | `upgrade head` 0001→0002→0003, `downgrade 0002`, `upgrade head`, `alembic check`: "No new upgrade operations detected" |
| Migrations (PostgreSQL, offline SQL) | 0003 renders `ALTER TABLE email_codes ADD COLUMN attempts INTEGER DEFAULT '0' NOT NULL` |
| pip-audit (API and worker requirements) | "No known vulnerabilities found" (both) |
| npm audit | `--omit=dev`: 2 (1 moderate, 1 high); all: 9 (3 moderate, 6 high). See below |
| Windows scripts | `dev-start.ps1`, `install-worker.ps1`, `run-worker.ps1`: 0 parse errors each |
| Production smoke test | API (`uvicorn`, port 8011) `/health/ready` → `{"ok":true,"migration":"0003","expected":"0003"}`, `/api/v1/plans` → 3 plans; web (`node .next/standalone/server.js`, port 3011): 15 pages (`/`, login, signup, pricing, forgot/reset password, app, bots, accounts, settings, alerts, admin, terms, privacy, risk) → 200, CSP present, `X-Frame-Options: DENY` |

### What the end-to-end worker test exercises (simulated MT5 terminal, real API server)
Sign-up → email code (read from the API log) → trial → bind an MT5 demo account to Rehman Bot → save the password
(write-only) → **real connection-check job** on a worker → start → the unchanged Rehman Bot strategy scans, the
platform approves order intents, orders fill, positions and trades reach the dashboard → admin views → tenant
isolation → close-all → kill the bot process: the instance is re-claimed with a new lease epoch, the old process is
fenced → diagnostics → stop (positions stay open) → wrong password is reported as an MT5 auth failure.

### Regression tests added for the review findings
Old refresh token cannot sign out new sessions after a password reset · login answers identically for locked /
unknown accounts · reset codes die after 5 wrong guesses · 2FA needs the current password and signs out older
sessions · a $0 invoice does not turn a trial into paid access · customer config rollback keeps admin-only values ·
close-all is refused while stopped and never carried into a later run · unacknowledged commands are re-delivered and
run once · order-intent replay is never re-approved after an outcome and re-checks pause/halt · stop loss / take
profit on the wrong side are refused · revoked accounts get no EA licence and cannot be reconfigured · viewers cannot
run diagnostics · WebSocket refuses access tokens in the URL · Telegram link tokens rotate and are single use ·
production checks fail closed for unknown `ENV` values (e.g. `PROD`) · entries and position management stop when the
local lease deadline passes · a cut-off supervisor stops (and if needed kills) its bots · management orders are
simulated in dry run · the order-result journal survives a lease loss during replay.

## Remaining npm audit findings (not fixed, documented)

* **Production dependencies (2):** `postcss <= 8.5.22` bundled inside `next` (high) and `next` itself (moderate, because
  of that bundled copy). The only fix offered is `next@16` (breaking upgrade). The bundled postcss runs at build time
  on our own CSS, not on user input.
* **Dev/build tooling (7 more):** `tailwindcss@3` and its file-watching/globbing dependencies (`braces`, `chokidar`,
  `fast-glob`, `micromatch`, `postcss-nested`, `postcss-selector-parser`). Build-time only; the fix is Tailwind 4.
* Plan: upgrade to Next 16 and Tailwind 4 in a separate, tested change.

## Known strategy behaviour (unchanged on purpose)
The Rehman Bot strategy code in `bots/rehman_bot` was not modified. One quirk observed in simulation: the TP1 leg is
sometimes refused by the strategy's own risk-reward gate ("RR too low: 1:0.5"), a floating-point edge where 0.5 ≥ 0.5
evaluates false. It only skips that leg; it is reported here for the strategy owner to decide.

## Not verified (needs resources that are not available here)

| Item | Why not | How to verify |
|---|---|---|
| Real PostgreSQL (JSONB, RLS policies, `nexfox_app` role) | by instruction, no database server was used | `docs/DEPLOYMENT.md` §1, then run the API suite with `DATABASE_URL=postgresql+psycopg://...` |
| Real MT5 terminal and broker demo account | no terminal installed; the only credentials found were for a live account and were not used | `docs/DEPLOYMENT.md` §2 and §3 (demo verification period) |
| Stripe, SMTP, Telegram | no credentials | configure them in `api.env`, then run a test payment, password reset and alert |
| Linux deployment scripts (`deploy/linux/*.sh`), nginx, systemd | no Linux host here; bash was not available to syntax-check | run `install.sh` / `deploy.sh` on a staging Ubuntu 24.04 server |
| `Stop-ScheduledTask` delivering a graceful stop to the worker | needs the real Windows task | stop the task on the VPS and confirm the instances show *stopped* (otherwise leases expire after 45 s) |
| Production deployment | no server or domain | — |
