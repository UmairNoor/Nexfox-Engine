# Deployment (no Docker)

Two kinds of hosts:

1. **Platform server** — Linux (Ubuntu 24.04): nginx, Next.js, FastAPI, scheduler, PostgreSQL 16, Redis, all as
   native systemd services. Managed PostgreSQL also works: point `DATABASE_URL` at it and skip the local database.
2. **MT5 worker host(s)** — Windows (Server 2019+/10/11 VPS): MetaTrader 5 terminals + `apps/worker`. Needed only for
   worker-hosted bots such as Rehman Bot. The MetaTrader5 Python package is Windows-only, so this cannot run on the
   Linux server.

You need: a domain with a DNS A record to the platform server, an email (SMTP) account, and — for paid plans — a
Stripe account. None of these are in the repository.

## 1. Platform server

```bash
git clone <your repo> /root/nexfox && cd /root/nexfox
sudo DOMAIN=app.example.com ADMIN_EMAIL=ops@example.com bash deploy/linux/install.sh
```
`install.sh` installs packages, creates the `nexfox` system user and `/opt/nexfox/{releases,shared}`, a PostgreSQL
owner role + database, generates secrets into `/opt/nexfox/shared/api.env` (once), installs systemd units, the nginx
site, firewall rules (22/80/443 only) and a Let's Encrypt certificate.

Edit `/opt/nexfox/shared/api.env`: `SMTP_*`, `BILLING_PROVIDER` (`manual` until Stripe is configured), Telegram.
Keep `ALLOW_LIVE_TRADING=false` until the demo verification period is complete.

```bash
sudo bash deploy/linux/deploy.sh          # build, backup, migrate, switch, health-check (auto-rollback on failure)
```

Row-level security (recommended): run from a root shell (`sudo -i`; `db-owner.env` is readable by root only), then
switch the API to the restricted role.
```bash
source /opt/nexfox/shared/db-owner.env
sudo -u postgres psql -d nexfox -v app_password="$APP_DB_PASSWORD" -f infra/sql/010_rls.sql
# api.env: DATABASE_URL=postgresql+psycopg://nexfox_app:<APP_DB_PASSWORD>@127.0.0.1:5432/nexfox
sudo systemctl restart nexfox-api nexfox-scheduler
```
(`deploy.sh` always migrates as the owner, so this stays correct across releases.)

Create the super-admin (password from the environment, TOTP secret printed once):
```bash
sudo -u nexfox bash -c 'cd /opt/nexfox/current/apps/api && set -a && . /opt/nexfox/shared/api.env && set +a && \
  ADMIN_PASSWORD="a-long-unique-password" .venv/bin/python -m app.cli create-admin you@example.com "Your Name"'
```

Stripe (optional): create three recurring prices, set `STRIPE_*`, `BILLING_PROVIDER=stripe`, and point a webhook at
`https://app.example.com/api/v1/billing/webhook` for `customer.subscription.*` and `invoice.paid|payment_failed`.

### Updating and rolling back
```bash
cd /root/nexfox && git pull && sudo bash deploy/linux/deploy.sh
sudo bash deploy/linux/rollback.sh            # previous release
sudo bash deploy/linux/rollback.sh 20261010120000
```
Code rollbacks keep the database schema (migrations are additive). Restoring data is a separate, deliberate step
(`docs/OPERATIONS.md`).

### Service control
```bash
systemctl status nexfox-api nexfox-scheduler nexfox-web
systemctl restart nexfox-api            # graceful: in-flight requests finish (20 s)
journalctl -u nexfox-api -f -o cat      # JSON log lines
curl -s https://app.example.com/health/ready
```

## 2. MT5 worker host (Windows)

1. Install Python 3.12+ (python.org, "Add to PATH") and Git.
2. Install MetaTrader 5 once per slot into separate folders (`C:\MT5\slot1`, `C:\MT5\slot2`, ...). Start each terminal
   once, then close it. Enable *Tools → Options → Expert Advisors → Allow algorithmic trading* in each.
3. In the admin console → **Workers** → *Register worker*; copy the token (shown once).
4. ```powershell
   git clone <your repo> C:\NexFox\src; cd C:\NexFox\src
   copy apps\worker\.env.example apps\worker\.env    # set NEXFOX_API_URL, WORKER_TOKEN, MT5_TERMINALS
   powershell -ExecutionPolicy Bypass -File deploy\windows\install-worker.ps1
   Start-ScheduledTask -TaskName NexFoxWorker
   ```
5. Enable Windows auto-logon for the worker user (MT5 terminals need a desktop session) and keep the VPS awake.

The task restarts the worker if it exits (backoff up to 2 min) and starts it at logon. Updating:
`git pull`, `Stop-ScheduledTask NexFoxWorker`,
`apps\worker\.venv\Scripts\python.exe -m pip install -r apps\worker\requirements.txt` (from `C:\NexFox\src`),
`Start-ScheduledTask NexFoxWorker`. Bots move to another worker automatically if this one goes away (lease expiry).

If the worker cannot reach the platform for about half a lease (≈22 s with the default 45 s), it stops its bots
itself, and each bot also refuses every order, including position management, once its local lease deadline passes.
So a cut-off host never trades on an account that another worker may have taken over. Positions keep their
broker-side SL/TP.

Worker capacity = number of terminal slots. Each account is pinned to one terminal folder
(`<WORKER_DATA_DIR>\terminals.json`, default `apps\worker\data\terminals.json`).

Not yet verified on a real host: whether `Stop-ScheduledTask` delivers a console signal to the Python process (graceful
stop and lease release). If it does not, the leases simply expire after 45 s and another worker takes over. Check it
once on your VPS: stop the task and confirm the instances show *stopped* in the console.

## 3. First verification on a demo account (before any customer)

1. Admin console → Platform: halt is off, live trading shows *disabled*.
2. Sign up a test customer, grant a plan (manual billing) or use the trial.
3. Connect an MT5 **demo** account with bot *Rehman Bot*, save the password, **Verify connection** → *Connected*.
4. Set *Dry run* on, start, watch Event log/Orders (orders appear as *Simulated*). Then dry run off on demo.
5. Watch for at least several trading days. Check Orders vs MT5 history, kill switch, close all, stop.
6. Only then consider `ALLOW_LIVE_TRADING=true`, and only with owner authorization per bot.
