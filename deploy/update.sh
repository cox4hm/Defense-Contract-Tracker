#!/usr/bin/env bash
# Apply a new version of the project: database changes, packages, restart.
#     bash deploy/update.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
set -a; . ./.env; set +a

echo "==> Updating tables, triggers and views"
for f in db/init/*.sql; do
    PGOPTIONS='--client-min-messages=warning' psql "$DATABASE_URL" -X -q -v ON_ERROR_STOP=1 -f "$f"
done

echo "==> Updating Python packages"
.venv/bin/pip install -q -r requirements.txt

echo "==> Restarting the dashboard"
sudo systemctl restart dct-web.service
echo "Done. Refresh http://localhost:${PORT:-8001}"
