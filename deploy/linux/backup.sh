#!/usr/bin/env bash
# PostgreSQL backup (custom format, compressed) with retention. Installed as /usr/local/bin/nexfox-backup and run daily
# by nexfox-backup.timer; deploy.sh also runs it before every migration.  Usage: nexfox-backup [label]
# Copy the files off the server too (e.g. rclone/restic to object storage) — a backup on the same disk is not enough.
set -euo pipefail
DIR=/opt/nexfox/shared/backups
DB=nexfox
KEEP_DAYS="${KEEP_DAYS:-14}"
LABEL="${1:-daily}"
FILE="$DIR/${DB}-$(date -u +%Y%m%dT%H%M%SZ)-${LABEL}.dump"
sudo -u postgres pg_dump -Fc -Z 6 -d "$DB" -f "$FILE"
sudo -u postgres pg_restore --list "$FILE" >/dev/null   # verify the archive is readable
find "$DIR" -name "${DB}-*.dump" -mtime +"$KEEP_DAYS" -delete
echo "backup ok: $FILE ($(du -h "$FILE" | cut -f1))"
