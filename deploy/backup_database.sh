#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${CANDYCAST_DB_PATH:-/var/lib/candycast/candycast.sqlite3}"
BACKUP_DIR="${CANDYCAST_BACKUP_DIR:-/var/backups/candycast}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${BACKUP_DIR}/candycast-${STAMP}.sqlite3"

install -d -m 0750 "${BACKUP_DIR}"
sqlite3 "${DB_PATH}" ".backup '${TARGET}'"
sha256sum "${TARGET}" | tee "${TARGET}.sha256"
chmod 0640 "${TARGET}" "${TARGET}.sha256"
printf '%s\n' "${TARGET}"
