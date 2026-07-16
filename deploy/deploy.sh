#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${CANDYCAST_APP_DIR:-/opt/candycast/app}"
VENV_DIR="${CANDYCAST_VENV_DIR:-/opt/candycast/venv}"
DB_PATH="${CANDYCAST_DB_PATH:-/var/lib/candycast/candycast.sqlite3}"
ENV_FILE="${CANDYCAST_ENV_FILE:-/etc/candycast/candycast.env}"

if [[ -r "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

cd "${APP_DIR}"
git fetch origin main
git checkout main
git pull --ff-only origin main

"${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
"${APP_DIR}/deploy/backup_database.sh"
"${VENV_DIR}/bin/python" standalone_pulseutv_server.py \
  --source "${APP_DIR}/site" \
  --site-dir "${APP_DIR}/site" \
  --db-path "${DB_PATH}" \
  --workdir /var/lib/candycast \
  --no-prepare \
  --prepare-only

sudo systemctl restart candycast
sleep 2
curl --fail --silent --show-error http://127.0.0.1:8770/ >/dev/null
systemctl --no-pager --full status candycast
