#!/usr/bin/env bash
# Backup di: DB utenti/MFA (MariaDB), configurazione Graylog (MongoDB),
# CA e certificati, file .env. NON include gli indici OpenSearch (i log)
# per motivi di spazio: valuta snapshot OpenSearch separati se ti servono.
#
# Pensato per essere lanciato da cron, es:
#   0 3 * * * /percorso/final-package/scripts/backup.sh >> /var/log/logplatform-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

BACKUP_DIR="${BACKUP_DIR:-/mnt/graylog-data/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
STAMP=$(date +%Y%m%d-%H%M%S)
DEST="${BACKUP_DIR}/${STAMP}"
mkdir -p "$DEST"

echo "[$STAMP] Backup MariaDB..."
docker compose exec -T mariadb mariadb-dump -u root -p"${MARIADB_ROOT_PASSWORD}" --all-databases \
  > "${DEST}/mariadb-all.sql"

echo "[$STAMP] Backup configurazione Graylog (MongoDB)..."
docker compose exec -T mongodb mongodump --archive > "${DEST}/mongodb-graylog.archive"

echo "[$STAMP] Backup CA/certificati e .env..."
tar -czf "${DEST}/ca-and-env.tar.gz" ca .env nginx/certs 2>/dev/null || true

echo "[$STAMP] Pulizia backup più vecchi di ${RETENTION_DAYS} giorni..."
find "$BACKUP_DIR" -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} \;

echo "[$STAMP] Backup completato in ${DEST}"
