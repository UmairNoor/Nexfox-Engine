#!/usr/bin/env bash
# Restore a backup into the nexfox database. DESTRUCTIVE: replaces current data. Asks for confirmation.
#   sudo bash deploy/linux/restore.sh /opt/nexfox/shared/backups/nexfox-YYYYmmddTHHMMSSZ-daily.dump
# Recommended drill: restore into a scratch database first:  RESTORE_DB=nexfox_restore_test sudo -E bash restore.sh FILE
set -euo pipefail
FILE="${1:?backup file}"
DB="${RESTORE_DB:-nexfox}"
[[ -f "$FILE" ]] || { echo "not found: $FILE"; exit 1; }
read -r -p "Restore $FILE into database '$DB'? Its current contents will be replaced. Type the database name to confirm: " ans
[[ "$ans" == "$DB" ]] || { echo "aborted"; exit 1; }
if [[ "$DB" == "nexfox" ]]; then
  systemctl stop nexfox-api nexfox-scheduler
  /usr/local/bin/nexfox-backup "before-restore"
fi
sudo -u postgres psql -qc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid<>pg_backend_pid();" || true
sudo -u postgres dropdb --if-exists "$DB"
sudo -u postgres createdb -O nexfox_owner "$DB"
sudo -u postgres pg_restore --no-owner --role=nexfox_owner -d "$DB" "$FILE"
if [[ "$DB" == "nexfox" ]]; then
  systemctl start nexfox-api nexfox-scheduler
  sleep 3; curl -fsS http://127.0.0.1:8000/health/ready && echo
fi
echo "restore complete into $DB. If RLS was configured, rerun infra/sql/010_rls.sql."
