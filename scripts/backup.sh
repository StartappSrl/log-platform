#!/usr/bin/env bash
# Backup completo per l'architettura reale (NS8/podman) - sostituisce lo
# script precedente, rimasto da una fase iniziale con docker-compose che
# non esiste piu' in questa infrastruttura (avrebbe fallito silenziosamente
# se mai lanciato).
#
# Include: MariaDB (utenti, tenant, impostazioni), MongoDB (configurazione
# Graylog), il volume della CA (certificati), il volume degli archivi
# caricati dagli agent, e i file di stato/segreti di ogni modulo NS8
# coinvolto (necessari per ricostruire la configurazione, non solo i dati).
#
# NON include gli indici OpenSearch (i log stessi) - stessa scelta dello
# script precedente, per motivi di spazio. Se ti servono, valuta uno
# snapshot OpenSearch separato (funzione nativa di OpenSearch).
#
# Pensato per girare da cron sul NODO (non dentro un container), es:
#   0 3 * * * /root/logmanager/final-package/scripts/backup.sh >> /var/log/logplatform-backup.log 2>&1
#
# NON TESTATO CON UN RIPRISTINO REALE - vedi RIPRISTINO.md per la
# procedura, che va provata almeno una volta su una macchina pulita
# prima di fidarsi ciecamente di questo backup in un'emergenza vera.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/root/logmanager-backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
STAMP=$(date +%Y%m%d-%H%M%S)
DEST="${BACKUP_DIR}/${STAMP}"
mkdir -p "$DEST"

echo "[$STAMP] Inizio backup in $DEST"

# --- Individua automaticamente le istanze dei moduli (i nomi hanno un
# numero finale assegnato da NS8, diverso da installazione a
# installazione - es. logplatform-mariadb1, non sempre lo stesso numero) ---
find_instance() {
    local prefix="$1"
    ls /home/ 2>/dev/null | grep "^${prefix}[0-9]*$" | head -1
}

MARIADB_INSTANCE=$(find_instance "logplatform-mariadb")
MONGODB_INSTANCE=$(find_instance "logplatform-mongodb")
AUTH_INSTANCE=$(find_instance "logplatform-auth")
GRAYLOG_INSTANCE=$(find_instance "logplatform-graylog")
GATE_INSTANCE=$(find_instance "logplatform-gate")

for name in MARIADB_INSTANCE MONGODB_INSTANCE AUTH_INSTANCE GRAYLOG_INSTANCE GATE_INSTANCE; do
    if [[ -z "${!name}" ]]; then
        echo "ATTENZIONE: non ho trovato l'istanza per $name - quella parte del backup verra' saltata." >&2
    fi
done

run_as() {
    local instance="$1"; shift
    sudo -u "$instance" XDG_RUNTIME_DIR="/run/user/$(id -u "$instance")" "$@"
}

# --- MariaDB: dump completo (utenti, tenant, impostazioni SMTP, audit log) ---
if [[ -n "$MARIADB_INSTANCE" ]]; then
    echo "[$STAMP] Backup MariaDB ($MARIADB_INSTANCE)..."
    MARIADB_ROOT_PASSWORD=$(grep -oP '(?<=MARIADB_ROOT_PASSWORD=).*' \
        "/home/${MARIADB_INSTANCE}/.config/state/secrets.env" 2>/dev/null || echo "")
    if [[ -n "$MARIADB_ROOT_PASSWORD" ]]; then
        run_as "$MARIADB_INSTANCE" podman exec logplatform-mariadb \
            mariadb-dump -u root -p"${MARIADB_ROOT_PASSWORD}" --all-databases \
            > "${DEST}/mariadb-all.sql" || echo "ATTENZIONE: dump MariaDB fallito." >&2
    else
        echo "ATTENZIONE: password root MariaDB non trovata in secrets.env - dump saltato." >&2
    fi
fi

# --- MongoDB: configurazione Graylog (stream, index set, allarmi, notifiche) ---
if [[ -n "$MONGODB_INSTANCE" ]]; then
    echo "[$STAMP] Backup MongoDB/Graylog ($MONGODB_INSTANCE)..."
    run_as "$MONGODB_INSTANCE" podman exec logplatform-mongodb \
        mongodump --archive > "${DEST}/mongodb-graylog.archive" \
        || echo "ATTENZIONE: dump MongoDB fallito." >&2
fi

# --- Volume della CA (certificati, chiave privata della CA) ---
if [[ -n "$AUTH_INSTANCE" ]]; then
    echo "[$STAMP] Backup volume CA..."
    run_as "$AUTH_INSTANCE" podman run --rm \
        -v logplatform-auth-ca:/data:ro \
        -v "${DEST}:/backup" \
        alpine tar -czf /backup/ca-volume.tar.gz -C /data . \
        || echo "ATTENZIONE: backup del volume CA fallito." >&2

    echo "[$STAMP] Backup volume archivi caricati..."
    run_as "$AUTH_INSTANCE" podman run --rm \
        -v logplatform-auth-archives:/data:ro \
        -v "${DEST}:/backup" \
        alpine tar -czf /backup/archives-volume.tar.gz -C /data . \
        || echo "ATTENZIONE: backup del volume archivi fallito (puo' essere molto grande - normale se impiega tempo)." >&2
fi

# --- File di stato/segreti di ogni modulo (necessari per ricostruire la
# configurazione: password, porte, riferimenti alle immagini) ---
echo "[$STAMP] Backup configurazione moduli NS8..."
mkdir -p "${DEST}/module-state"
for instance in "$MARIADB_INSTANCE" "$MONGODB_INSTANCE" "$AUTH_INSTANCE" "$GRAYLOG_INSTANCE" "$GATE_INSTANCE"; do
    if [[ -n "$instance" && -d "/home/${instance}/.config/state" ]]; then
        cp -r "/home/${instance}/.config/state" "${DEST}/module-state/${instance}" 2>/dev/null || true
    fi
done

# --- Pulizia backup vecchi ---
echo "[$STAMP] Pulizia backup più vecchi di ${RETENTION_DAYS} giorni..."
find "$BACKUP_DIR" -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} \; 2>/dev/null || true

echo "[$STAMP] Backup completato in ${DEST}"
du -sh "${DEST}" 2>/dev/null || true
