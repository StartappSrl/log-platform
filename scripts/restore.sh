#!/usr/bin/env bash
# Ripristina un backup creato da backup.sh.
# Uso: ./restore.sh /mnt/graylog-data/backups/20260101-030000
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

BACKUP_PATH="${1:?Uso: $0 /percorso/backup/<timestamp>}"
[[ -d "$BACKUP_PATH" ]] || { echo "Cartella backup non trovata: $BACKUP_PATH" >&2; exit 1; }

echo "ATTENZIONE: questo sovrascriverà i dati attuali di MariaDB, MongoDB, CA e .env."
read -rp "Confermi? (scrivi SI in maiuscolo): " CONFIRM
[[ "$CONFIRM" == "SI" ]] || { echo "Annullato."; exit 1; }

echo "Ripristino MariaDB..."
docker compose exec -T mariadb mariadb -u root -p"${MARIADB_ROOT_PASSWORD}" < "${BACKUP_PATH}/mariadb-all.sql"

echo "Ripristino configurazione Graylog (MongoDB)..."
docker compose exec -T mongodb mongorestore --archive --drop < "${BACKUP_PATH}/mongodb-graylog.archive"

echo "Ripristino CA/certificati/.env (in ./restore-tmp, verifica prima di sovrascrivere)..."
mkdir -p restore-tmp
tar -xzf "${BACKUP_PATH}/ca-and-env.tar.gz" -C restore-tmp
echo "Controlla ./restore-tmp e copia manualmente ca/, .env, nginx/certs/ se corretti."

echo "Riavvia lo stack: docker compose restart"
