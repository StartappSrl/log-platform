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

FAILURES=()
record_failure() {
    local msg="$1"
    echo "ATTENZIONE: $msg" >&2
    FAILURES+=("$msg")
}

BACKUP_DIR="${BACKUP_DIR:-/var/lib/logplatform-backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
STAMP=$(date +%Y%m%d-%H%M%S)
DEST="${BACKUP_DIR}/${STAMP}"
mkdir -p "$BACKUP_DIR"
# 711: serve il permesso di ATTRAVERSAMENTO su ogni cartella del percorso
# perche' gli utenti dei vari moduli (mariadb, mongodb, auth) raggiungano
# $DEST piu' sotto quando lanciano i loro comandi - 711 permette di
# attraversare ma non di elencare il contenuto di questa cartella
# principale, un compromesso ragionevole.
chmod 711 "$BACKUP_DIR"
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
        record_failure "non ho trovato l'istanza per $name - quella parte del backup e' stata saltata."
    fi
done

run_as() {
    local instance="$1"; shift
    # cd in una cartella accessibile a tutti PRIMA di sudo -u, altrimenti
    # fallisce con "cannot chdir" se lanciato da una cartella di root
    # (es. dentro il repository) - stesso problema incontrato piu' volte
    # con i comandi manuali in questa sessione.
    (cd /tmp && sudo -u "$instance" XDG_RUNTIME_DIR="/run/user/$(id -u "$instance")" "$@")
}

# --- MariaDB: dump completo (utenti, tenant, impostazioni SMTP, audit log) ---
if [[ -n "$MARIADB_INSTANCE" ]]; then
    echo "[$STAMP] Backup MariaDB ($MARIADB_INSTANCE)..."
    MARIADB_ROOT_PASSWORD=$(grep -oP '(?<=MARIADB_ROOT_PASSWORD=).*' \
        "/home/${MARIADB_INSTANCE}/.config/state/secrets.env" 2>/dev/null || echo "")
    if [[ -n "$MARIADB_ROOT_PASSWORD" ]]; then
        run_as "$MARIADB_INSTANCE" podman exec logplatform-mariadb \
            mariadb-dump -u root -p"${MARIADB_ROOT_PASSWORD}" --all-databases \
            > "${DEST}/mariadb-all.sql" || record_failure "dump MariaDB fallito."
    else
        record_failure "password root MariaDB non trovata in secrets.env - dump saltato."
    fi
fi

# --- MongoDB: configurazione Graylog (stream, index set, allarmi, notifiche) ---
if [[ -n "$MONGODB_INSTANCE" ]]; then
    echo "[$STAMP] Backup MongoDB/Graylog ($MONGODB_INSTANCE)..."
    run_as "$MONGODB_INSTANCE" podman exec logplatform-mongodb \
        mongodump --archive > "${DEST}/mongodb-graylog.archive" \
        || record_failure "dump MongoDB fallito."
fi

# --- Volume della CA (certificati, chiave privata della CA) ---
# --- Volume della CA e degli archivi: uso 'podman exec' dentro il
# container auth-service GIA' in esecuzione (che ha gia' accesso
# legittimo a questi volumi tramite i propri mount), invece di un nuovo
# container con bind-mount ad-hoc - niente problemi di rietichettatura
# SELinux, stesso principio che ha gia' funzionato per MariaDB/MongoDB
# (stream diretto via stdout, nessun volume temporaneo). ---
if [[ -n "$AUTH_INSTANCE" ]]; then
    echo "[$STAMP] Backup volume CA..."
    run_as "$AUTH_INSTANCE" podman exec logplatform-auth tar -czf - -C /data/ca . \
        > "${DEST}/ca-volume.tar.gz" \
        || record_failure "backup del volume CA fallito."

    echo "[$STAMP] Backup volume archivi caricati..."
    run_as "$AUTH_INSTANCE" podman exec logplatform-auth tar -czf - -C /data/archives . \
        > "${DEST}/archives-volume.tar.gz" \
        || record_failure "backup del volume archivi fallito."
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

# Richiude i permessi ora che tutti i file sono stati scritti - un dump
# completo di MariaDB contiene password con hash, non deve restare
# leggibile da chiunque anche solo per il tempo tra un'esecuzione e l'altra.
# (chmod separato per file e cartelle: 600 su una cartella toglierebbe
# anche il permesso di attraversarla)
find "$DEST" -type f -exec chmod 600 {} \; 2>/dev/null || true
find "$DEST" -type d -exec chmod 700 {} \; 2>/dev/null || true

du -sh "${DEST}" 2>/dev/null || true

if [[ ${#FAILURES[@]} -gt 0 ]]; then
    echo "[$STAMP] ${#FAILURES[@]} problema/i durante il backup, invio avviso..."
    if [[ -n "$AUTH_INSTANCE" ]]; then
        BODY="Il backup del $(date '+%d/%m/%Y %H:%M') ha avuto ${#FAILURES[@]} problema/i:

$(printf ' - %s\n' "${FAILURES[@]}")

Il backup e' comunque completato per le parti riuscite, in ${DEST}.
Controlla il file manualmente prima di fidartene per un ripristino."
        run_as "$AUTH_INSTANCE" podman exec logplatform-auth \
            python3 -m app.send_alert_email "Backup incompleto su $(hostname)" "$BODY" \
            || echo "ATTENZIONE: non sono riuscito nemmeno a mandare l'avviso del fallimento (vedi sopra)." >&2
    else
        echo "ATTENZIONE: istanza auth non trovata, impossibile inviare l'avviso di backup fallito." >&2
    fi
fi
