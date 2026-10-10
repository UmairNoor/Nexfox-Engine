#!/usr/bin/env bash
# Switch back to the previous release (or a named one) and restart.  sudo bash deploy/linux/rollback.sh [RELEASE_TS]
# Code only: the database stays at its current (newer, additive) schema. Restore a backup only if a migration was faulty.
set -euo pipefail
BASE=/opt/nexfox
CUR="$(readlink -f "$BASE/current")"
if [[ -n "${1:-}" ]]; then TARGET="$BASE/releases/$1"; else TARGET="$(ls -1dt "$BASE"/releases/* | grep -v "^$CUR$" | head -n1)"; fi
[[ -d "$TARGET" ]] || { echo "no such release: $TARGET"; exit 1; }
echo "rolling back: $CUR -> $TARGET"
ln -sfn "$TARGET" "$BASE/current.new" && mv -Tf "$BASE/current.new" "$BASE/current"
systemctl restart nexfox-api nexfox-scheduler nexfox-web
sleep 3
curl -fsS http://127.0.0.1:8000/health/ready && echo && echo "rollback complete"
