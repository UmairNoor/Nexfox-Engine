#!/usr/bin/env bash
# One-time server preparation for NexFox Engine (web + API + scheduler + PostgreSQL + nginx). No Docker.
# Tested target: Ubuntu 24.04 LTS. Run as root:
#   sudo DOMAIN=app.example.com ADMIN_EMAIL=ops@example.com bash deploy/linux/install.sh
# Then deploy code with deploy/linux/deploy.sh. Nothing here contacts a broker or enables live trading.
set -euo pipefail

DOMAIN="${DOMAIN:?set DOMAIN=your.domain}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
APP_USER=nexfox
BASE=/opt/nexfox
DB_NAME=nexfox
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> packages"
apt-get update -y
apt-get install -y --no-install-recommends python3 python3-venv python3-dev build-essential postgresql postgresql-contrib \
  nginx certbot python3-certbot-nginx curl ca-certificates rsync ufw redis-server
if ! command -v node >/dev/null || [[ "$(node -v | cut -c2- | cut -d. -f1)" -lt 20 ]]; then
  echo "==> Node.js 22 (NodeSource)"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

echo "==> user and directories"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$BASE" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$BASE" "$BASE/releases" "$BASE/shared" "$BASE/shared/downloads"
install -d -o postgres -g "$APP_USER" -m 770 "$BASE/shared/backups"

echo "==> PostgreSQL role and database"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='nexfox_owner'" | grep -q 1; then
  OWNER_PW="$(openssl rand -hex 24)"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE nexfox_owner LOGIN PASSWORD '${OWNER_PW}'"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${DB_NAME} OWNER nexfox_owner"
  APP_PW="$(openssl rand -hex 24)"
  echo "OWNER_URL=postgresql+psycopg://nexfox_owner:${OWNER_PW}@127.0.0.1:5432/${DB_NAME}" > "$BASE/shared/db-owner.env"
  echo "APP_DB_PASSWORD=${APP_PW}" >> "$BASE/shared/db-owner.env"
  chmod 600 "$BASE/shared/db-owner.env"; chown root:root "$BASE/shared/db-owner.env"
fi
source "$BASE/shared/db-owner.env"

echo "==> API environment (secrets generated once, never overwritten)"
if [[ ! -f "$BASE/shared/api.env" ]]; then
  CRED_KEY="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  sed -e "s|__DOMAIN__|${DOMAIN}|g" \
      -e "s|__APP_SECRET__|$(openssl rand -hex 32)|" \
      -e "s|__EA_SECRET__|$(openssl rand -hex 32)|" \
      -e "s|__CRED_KEY__|${CRED_KEY}|" \
      -e "s|__DATABASE_URL__|${OWNER_URL}|" \
      "$HERE/env/api.env.example" > "$BASE/shared/api.env"
  chmod 640 "$BASE/shared/api.env"; chown root:"$APP_USER" "$BASE/shared/api.env"
  echo "    wrote $BASE/shared/api.env — review BILLING_PROVIDER, SMTP_* and TELEGRAM_* before going live"
fi
if [[ ! -f "$BASE/shared/web.env" ]]; then
  sed -e "s|__DOMAIN__|${DOMAIN}|g" "$HERE/env/web.env.example" > "$BASE/shared/web.env"
  chown root:"$APP_USER" "$BASE/shared/web.env"; chmod 640 "$BASE/shared/web.env"
fi

echo "==> systemd units"
install -m 644 "$HERE"/systemd/*.service "$HERE"/systemd/*.timer /etc/systemd/system/
install -d /etc/systemd/journald.conf.d
install -m 644 "$HERE/journald-nexfox.conf" /etc/systemd/journald.conf.d/nexfox.conf
install -m 755 "$HERE/backup.sh" /usr/local/bin/nexfox-backup
systemctl daemon-reload
systemctl restart systemd-journald
systemctl enable nexfox-api nexfox-scheduler nexfox-web nexfox-backup.timer redis-server

echo "==> nginx"
sed -e "s|__DOMAIN__|${DOMAIN}|g" "$HERE/nginx/nexfox.conf" > /etc/nginx/sites-available/nexfox.conf
ln -sf /etc/nginx/sites-available/nexfox.conf /etc/nginx/sites-enabled/nexfox.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> firewall (SSH, HTTP, HTTPS only; PostgreSQL and Redis stay on localhost)"
ufw allow OpenSSH >/dev/null; ufw allow 'Nginx Full' >/dev/null; ufw --force enable >/dev/null

echo "==> TLS certificate"
if [[ -n "$ADMIN_EMAIL" ]]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$ADMIN_EMAIL" --redirect || echo "    certbot failed: check DNS for $DOMAIN, then rerun: certbot --nginx -d $DOMAIN"
else
  echo "    skipped (set ADMIN_EMAIL to obtain a Let's Encrypt certificate): certbot --nginx -d $DOMAIN"
fi

cat <<EOF

Server prepared. Next:
  1) sudo bash deploy/linux/deploy.sh                 # build, migrate, start
  2) sudo -i, then: source $BASE/shared/db-owner.env && sudo -u postgres psql -d ${DB_NAME} -v app_password="\$APP_DB_PASSWORD" -f infra/sql/010_rls.sql
     then set DATABASE_URL in $BASE/shared/api.env to the nexfox_app role (see docs/DEPLOYMENT.md) and restart nexfox-api
  3) sudo -u ${APP_USER} bash -c 'cd $BASE/current/apps/api && set -a && . $BASE/shared/api.env && ADMIN_PASSWORD=... .venv/bin/python -m app.cli create-admin you@example.com "Your Name"'
EOF
