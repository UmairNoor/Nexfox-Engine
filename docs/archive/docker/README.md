# Archived Docker files (not used)

The platform is deployed natively (systemd + nginx on Linux, a Windows host for MT5 workers). See `docs/DEPLOYMENT.md`.

These files are kept only as a record of the original development stack. What they contained was carried over:

| Docker setting | Native replacement |
|---|---|
| `db: timescale/timescaledb:pg16`, user/db `aurum` | PostgreSQL 16 service; `deploy/linux/install.sh` creates role + database `nexfox` |
| `migrate` service running `010_timescale_rls.sql` | `python -m app.cli migrate` (Alembic) + `infra/sql/010_rls.sql`; TimescaleDB optional (`020_timescale_optional.sql`) |
| `redis:7` | Optional (`REDIS_URL`), only for multi-replica websocket fan-out |
| `api` (uvicorn :8000, `--proxy-headers`) | `nexfox-api.service` (uvicorn bound to 127.0.0.1:8000, behind nginx) |
| `web` (Next standalone :3000, build arg `NEXT_PUBLIC_API_URL`) | `nexfox-web.service` (`node server.js`), `NEXT_PUBLIC_API_URL` set at build in `deploy.sh` |
| volume `./apps/api/downloads` | `/opt/nexfox/shared/downloads` (`EA_DOWNLOAD_DIR`) |
