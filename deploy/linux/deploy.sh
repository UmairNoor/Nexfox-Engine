#!/usr/bin/env bash
# Build and release NexFox Engine from a source checkout, with automatic rollback if the new release is unhealthy.
#   sudo bash deploy/linux/deploy.sh [SOURCE_DIR]        (default: the repository this script lives in)
# Steps: copy source -> new release dir -> venv + pip -> npm ci + build -> DB backup -> migrations -> switch
#        symlink -> restart -> health check -> (rollback on failure). Releases live in /opt/nexfox/releases.
# Database migrations are additive; a code rollback does not undo them (the previous code runs on the newer schema).
set -euo pipefail

SRC="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
BASE=/opt/nexfox
APP_USER=nexfox
KEEP=5
TS="$(date -u +%Y%m%d%H%M%S)"
REL="$BASE/releases/$TS"
PREV="$(readlink -f "$BASE/current" 2>/dev/null || true)"
source "$BASE/shared/db-owner.env"

echo "==> release $TS from $SRC"
install -d -o "$APP_USER" -g "$APP_USER" "$REL"
rsync -a --delete --exclude '.venv' --exclude 'node_modules' --exclude '.next' --exclude '*.db' --exclude '__pycache__' \
  --exclude 'apps/worker' --exclude 'apps/prototype' --exclude 'docs/archive' --exclude '.env' "$SRC/" "$REL/"
chown -R "$APP_USER:$APP_USER" "$REL"
as_app() { sudo -u "$APP_USER" -H bash -c "$1"; }

echo "==> API dependencies"
as_app "cd '$REL/apps/api' && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt"

echo "==> web build"
set -a; source "$BASE/shared/web.env"; set +a
as_app "cd '$REL/apps/web' && export NEXT_PUBLIC_API_URL='$NEXT_PUBLIC_API_URL' NEXT_PUBLIC_APP_NAME='${NEXT_PUBLIC_APP_NAME:-NexFox Engine}' NEXT_PUBLIC_SUPPORT_EMAIL='${NEXT_PUBLIC_SUPPORT_EMAIL:-}' NEXT_TELEMETRY_DISABLED=1 && npm ci --no-audit --no-fund && npm run build"
as_app "cd '$REL/apps/web' && cp -r .next/static .next/standalone/.next/static && cp -r public .next/standalone/public"

echo "==> production configuration check"
as_app "cd '$REL/apps/api' && set -a && . '$BASE/shared/api.env' && set +a && .venv/bin/python -c 'from app.config import get_settings; import sys; p=get_settings().validate_for_production(); [print(\"PROBLEM:\",x) for x in p]; sys.exit(1 if p else 0)'"

echo "==> database backup before migrating"
/usr/local/bin/nexfox-backup "pre-deploy-$TS"

echo "==> migrations (as the database owner)"
( cd "$REL/apps/api" && set -a && . "$BASE/shared/api.env" && set +a && DATABASE_URL="$OWNER_URL" .venv/bin/python -m app.cli migrate \
  && DATABASE_URL="$OWNER_URL" .venv/bin/python -m app.cli seed )
# tables created by migrations must stay readable by the RLS-restricted app role (no-op until 010_rls.sql has run)
sudo -u postgres psql -d nexfox -qc "DO \$\$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='nexfox_app') THEN
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nexfox_app; GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nexfox_app; END IF; END \$\$;" || true

echo "==> switch to the new release"
ln -sfn "$REL" "$BASE/current.new" && mv -Tf "$BASE/current.new" "$BASE/current"
systemctl restart nexfox-api nexfox-scheduler nexfox-web

echo "==> health check"
ok=0
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health/ready >/dev/null && curl -fsS -o /dev/null http://127.0.0.1:3000/; then ok=1; break; fi
  sleep 2
done
if [[ "$ok" != 1 ]]; then
  echo "!! new release is unhealthy"; journalctl -u nexfox-api -n 40 --no-pager || true
  if [[ -n "$PREV" && -d "$PREV" ]]; then
    echo "!! rolling back to $PREV"
    ln -sfn "$PREV" "$BASE/current.new" && mv -Tf "$BASE/current.new" "$BASE/current"
    systemctl restart nexfox-api nexfox-scheduler nexfox-web
  fi
  exit 1
fi

echo "==> pruning old releases (keeping $KEEP)"
ls -1dt "$BASE"/releases/* | tail -n +$((KEEP + 1)) | xargs -r rm -rf
echo "Release $TS is live."
