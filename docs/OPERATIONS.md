# Operations: monitoring, backups, recovery, runbooks

## Health and monitoring

| Signal | Where |
|---|---|
| Liveness | `GET /health` (process up) |
| Readiness | `GET /health/ready` (DB reachable + schema at expected migration) — use for uptime checks |
| Operational alerts | Admin → Overview: failed/offline bots, uncertain orders, silent workers, stuck jobs, error rate |
| Worker hosts | Admin → Workers (online, version, capabilities, free slots, last sync) |
| Logs | journald, one JSON object per line: `ts level env service module message correlation_id tenant_id instance_id bot_code event code` |
| Audit | Admin → Audit log (all admin actions, logins, config changes, live authorizations) |
| Customer alerts | `notifications` table, Telegram/email per tenant preferences |

Useful queries: `journalctl -u nexfox-api -o cat | jq 'select(.level=="ERROR")'`; find one request by its
`X-Request-ID`/`correlation_id` shown to users in error messages.

## Backups

* Daily `pg_dump -Fc` at 02:30 UTC (`nexfox-backup.timer`), verified with `pg_restore --list`, kept 14 days in
  `/opt/nexfox/shared/backups`; also before every deploy's migrations.
* **Copy backups off the server** (restic/rclone to object storage) — required for disaster recovery.
* Monthly restore drill: `RESTORE_DB=nexfox_restore_test sudo -E bash deploy/linux/restore.sh <file>`.
* Worker hosts keep only re-creatable state (terminal pinning, level-tracker cache, order-result journal);
  the order-result journal is replayed automatically on restart.

## Retention

| Data | Default | Setting |
|---|---|---|
| EA heartbeats | 14 days | `RETENTION_HEARTBEATS_DAYS` |
| Bot events | 90 days | `RETENTION_BOT_EVENTS_DAYS` |
| Notifications | 90 days | `RETENTION_NOTIFICATIONS_DAYS` |
| AI task inputs/outputs | 180 days | `RETENTION_AI_TASKS_DAYS` |
| Orders, positions, runs, invoices, audit log | kept | (accounting/security records) |

## Graceful shutdown semantics

* API: uvicorn finishes in-flight requests (20 s).
* Scheduler: finishes the current tick, releases its leader lease.
* Worker: asks every bot process to stop; strategies end, **positions stay open with broker SL/TP**; leases are
  released through a final sync. A killed worker is detected by lease expiry (45 s) and its bots are re-claimed by
  another worker with a new lease epoch.

## Runbooks

**Emergency: stop all new trading** — Admin → Platform → *Halt all trading* (reason required). Takes effect on the next
EA heartbeat / worker order request. Nothing is closed. To flatten a specific bot: instance → *Close all positions*.

**A bot shows "Order outcome unknown"** — the worker reconciles automatically (positions + deal history by order id).
If it persists: check the account in MT5 for a position with `#<order id>` in the comment; the instance's Orders tab
shows the id. New entries stay blocked until resolved. Never resubmit manually without checking.

**Worker host down** — bots go *offline* after the lease (45 s) and resume on another worker if one has free slots.
Check Admin → Workers; restart the scheduled task on the host; RDP in if MT5 needs attention.

**Repeated "Login failed"** — the customer must re-enter the MT5 password; after 5 consecutive failures the bot is
stopped automatically (no retry storm against the broker).

**Bad bot release** — Admin → Bots: set the previous approved version active, move affected instances back (stop
first), halt the bad version. Positions and history are untouched. Then follow docs/BOT_INTEGRATION.md §5.

**Bad platform release** — `sudo bash deploy/linux/rollback.sh`. If a migration damaged data: stop API/scheduler,
`restore.sh` the pre-deploy backup (`pre-deploy-<ts>`), redeploy the matching release.

**Suspected credential leak** — rotate: worker tokens (Admin → Workers → Rotate), `APP_SECRET` (signs everyone out),
`EA_SIGNING_SECRET` (requires a new EA build), `MT5_CREDENTIALS_KEY` (customers must re-enter MT5 passwords).
