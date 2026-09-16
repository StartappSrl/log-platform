#!/usr/bin/env bash
# Ripristina un backup creato da backup.sh (versione NS8/podman) -
# sostituisce lo script precedente, rimasto da una fase con
# docker-compose che non esiste piu' in questa infrastruttura.
#
# Uso: ./restore.sh /var/lib/logplatform-backups/<timestamp>
#
# NON TESTATO SU UN VERO RIPRISTINO - stessa cautela di backup.sh, provalo
# su una macchina di test prima di fidartene per un'emergenza reale.

set -euo pipefail

BACKUP_PATH="${1:?Uso: $0 /percorso/backup/<timestamp>}"
[[ -d "$BACKUP_PATH" ]] || { echo "Cartella backup non trovata: $BACKUP_PATH" >&2; exit 1; }

echo "ATTENZIONE: questo sovrascriverà i dati attuali di MariaDB, MongoDB,"
echo "la CA e gli archivi caricati dagli agent. Non c'è modo di annullare"
echo "questa operazione una volta fatta."
read -rp "Confermi? (scrivi SI in maiuscolo): " CONFIRM
[[ "$CONFIRM" == "SI" ]] || { echo "Annullato."; exit 1; }

find_instance() {
    local prefix="$1"
    ls /home/ 2>/dev/null | grep "^${prefix}[0-9]*$" | head -1
}

MARIADB_INSTANCE=$(find_instance "logplatform-mariadb")
MONGODB_INSTANCE=$(find_instance "logplatform-mongodb")
AUTH_INSTANCE=$(find_instance "logplatform-auth")

run_as() {
    local instance="$1"; shift
    (cd /tmp && sudo -u "$instance" XDG_RUNTIME_DIR="/run/user/$(id -u "$instance")" "$@")
}

# --- MariaDB ---
if [[ -n "$MARIADB_INSTANCE" && -f "${BACKUP_PATH}/mariadb-all.sql" ]]; then
    echo "Ripristino MariaDB ($MARIADB_INSTANCE)..."
    MARIADB_ROOT_PASSWORD=$(grep -oP '(?<=MARIADB_ROOT_PASSWORD=).*' \
        "/home/${MARIADB_INSTANCE}/.config/state/secrets.env" 2>/dev/null || echo "")
    if [[ -n "$MARIADB_ROOT_PASSWORD" ]]; then
        run_as "$MARIADB_INSTANCE" podman cp "${BACKUP_PATH}/mariadb-all.sql" logplatform-mariadb:/tmp/restore.sql
        run_as "$MARIADB_INSTANCE" podman exec logplatform-mariadb \
            mariadb -u root -p"${MARIADB_ROOT_PASSWORD}" -e "source /tmp/restore.sql"
        echo "MariaDB ripristinato."
    else
        echo "ATTENZIONE: password root MariaDB non trovata - ripristino MariaDB saltato." >&2
    fi
else
    echo "File mariadb-all.sql non trovato nel backup, salto questa parte." >&2
fi

# --- MongoDB (configurazione Graylog) ---
if [[ -n "$MONGODB_INSTANCE" && -f "${BACKUP_PATH}/mongodb-graylog.archive" ]]; then
    echo "Ripristino MongoDB/Graylog ($MONGODB_INSTANCE)..."
    run_as "$MONGODB_INSTANCE" podman cp "${BACKUP_PATH}/mongodb-graylog.archive" logplatform-mongodb:/tmp/restore.archive
    run_as "$MONGODB_INSTANCE" podman exec logplatform-mongodb \
        mongorestore --archive=/tmp/restore.archive --drop
    echo "MongoDB ripristinato."
else
    echo "File mongodb-graylog.archive non trovato nel backup, salto questa parte." >&2
fi

# --- Volume CA ---
if [[ -n "$AUTH_INSTANCE" && -f "${BACKUP_PATH}/ca-volume.tar.gz" ]]; then
    echo "Ripristino volume CA..."
    echo "  (sovrascrive la CA esistente - i certificati agent gia' distribuiti"
    echo "   con una CA DIVERSA smetteranno di funzionare finche' non li rigeneri)"
    run_as "$AUTH_INSTANCE" podman cp "${BACKUP_PATH}/ca-volume.tar.gz" logplatform-auth:/tmp/ca-restore.tar.gz
    run_as "$AUTH_INSTANCE" podman exec logplatform-auth \
        sh -c "rm -rf /data/ca/* && tar -xzf /tmp/ca-restore.tar.gz -C /data/ca"
    echo "Volume CA ripristinato."
else
    echo "File ca-volume.tar.gz non trovato nel backup, salto questa parte." >&2
fi

# --- Volume archivi ---
if [[ -n "$AUTH_INSTANCE" && -f "${BACKUP_PATH}/archives-volume.tar.gz" ]]; then
    echo "Ripristino volume archivi..."
    run_as "$AUTH_INSTANCE" podman cp "${BACKUP_PATH}/archives-volume.tar.gz" logplatform-auth:/tmp/archives-restore.tar.gz
    run_as "$AUTH_INSTANCE" podman exec logplatform-auth \
        sh -c "rm -rf /data/archives/* && tar -xzf /tmp/archives-restore.tar.gz -C /data/archives"
    echo "Volume archivi ripristinato."
else
    echo "File archives-volume.tar.gz non trovato nel backup, salto questa parte." >&2
fi

echo
echo "Ripristino completato. Riavvia i moduli coinvolti:"
echo "  sudo -u $MARIADB_INSTANCE XDG_RUNTIME_DIR=/run/user/\$(id -u $MARIADB_INSTANCE) systemctl --user restart logplatform-mariadb.service"
echo "  sudo -u $MONGODB_INSTANCE XDG_RUNTIME_DIR=/run/user/\$(id -u $MONGODB_INSTANCE) systemctl --user restart logplatform-mongodb.service"
echo "  sudo -u $AUTH_INSTANCE XDG_RUNTIME_DIR=/run/user/\$(id -u $AUTH_INSTANCE) systemctl --user restart logplatform-auth.service"
echo
echo "Poi verifica: login nel pannello, clienti/stream presenti, un pacchetto"
echo "agent scaricato ora usa lo stesso certificato CA di prima."
