#!/usr/bin/env bash
# One-time setup on Ubuntu (22.04 / 24.04). Run from inside the project folder:
#     bash deploy/setup_ubuntu.sh
# Installs PostgreSQL if needed, creates the database, loads the data and
# starts the dashboard at http://localhost:8001
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(whoami)"
DB_NAME="defense_tracker"
DB_USER="defense"
cd "$APP_DIR"

echo "==> Installing system packages (asks for your password)"
sudo apt-get update -qq
sudo apt-get install -y -qq postgresql python3-venv python3-pip curl >/dev/null
sudo systemctl enable --now postgresql

echo "==> Creating the database"
if [ ! -f .env ]; then
    DB_PASS="$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
    ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
    cat > .env <<EOF
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}
ADMIN_TOKEN=${ADMIN_TOKEN}
PORT=8001
REQUEST_PAUSE_SECONDS=0.4
LOG_LEVEL=INFO
EOF
    chmod 600 .env
    sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<EOF
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  ELSE
    ALTER ROLE ${DB_USER} PASSWORD '${DB_PASS}';
  END IF;
END \$\$;
EOF
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
        sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
    fi
else
    echo "    .env already exists, keeping it"
fi
set -a; . ./.env; set +a

echo "==> Creating tables, triggers and views"
for f in db/init/*.sql; do
    PGOPTIONS='--client-min-messages=warning' psql "$DATABASE_URL" -X -q -v ON_ERROR_STOP=1 -f "$f"
done

echo "==> Installing Python packages"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p logs

echo "==> Loading data from USAspending.gov"
echo "    The first load reads a few thousand contract pages and can take 30-60 minutes."
echo "    Progress is printed as it goes; the dashboard works as soon as the first step finishes."
.venv/bin/python -m app.loader --source setup

echo "==> Installing the daily update and the web service"
for unit in dct-loader.service dct-loader.timer dct-web.service; do
    sed -e "s#__USER__#${APP_USER}#g" -e "s#__APP_DIR__#${APP_DIR}#g" "deploy/${unit}" \
        | sudo tee "/etc/systemd/system/${unit}" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now dct-loader.timer
sudo systemctl enable --now dct-web.service
sleep 2

echo
echo "Done! Open http://localhost:${PORT:-8001}"
echo "Admin page: http://localhost:${PORT:-8001}/admin"
echo "Admin token: ${ADMIN_TOKEN}   (also saved in .env)"
