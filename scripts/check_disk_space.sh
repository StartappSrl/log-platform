#!/usr/bin/env bash
# Controlla lo spazio disco sul NODO (non dentro un container - da li'
# non si vede il disco reale dove vivono gli indici OpenSearch, che sono
# la parte piu' a rischio di riempire tutto). Manda un avviso email
# (riusando la configurazione SMTP gia' salvata nel pannello) se
# l'utilizzo supera la soglia.
#
# Pensato per girare via cron ogni ora, es:
#   0 * * * * /root/logmanager/final-package/scripts/check_disk_space.sh >> /var/log/logplatform-disk-check.log 2>&1

set -euo pipefail

THRESHOLD_PERCENT="${DISK_THRESHOLD_PERCENT:-85}"
AUTH_INSTANCE="${AUTH_INSTANCE:-}"

if [[ -z "$AUTH_INSTANCE" ]]; then
    AUTH_INSTANCE=$(ls /home/ 2>/dev/null | grep "^logplatform-auth[0-9]*$" | head -1)
fi

send_alert() {
    local subject="$1"
    local body="$2"
    if [[ -n "$AUTH_INSTANCE" ]]; then
        sudo -u "$AUTH_INSTANCE" XDG_RUNTIME_DIR="/run/user/$(id -u "$AUTH_INSTANCE")" \
            podman exec logplatform-auth python3 -m app.send_alert_email "$subject" "$body" \
            || echo "ATTENZIONE: invio avviso fallito (vedi sopra per il motivo)." >&2
    else
        echo "ATTENZIONE: istanza auth non trovata, impossibile inviare l'avviso." >&2
    fi
}

# Controlla il filesystem radice (dove tipicamente vive /var/lib/containers/storage
# con tutti i volumi podman, inclusi gli indici OpenSearch) - se la tua
# installazione usa un mount separato per i dati, aggiusta il percorso
# qui sotto per puntare a quello invece.
CHECK_PATH="${DISK_CHECK_PATH:-/}"

USAGE_PERCENT=$(df -P "$CHECK_PATH" | awk 'NR==2 {gsub("%","",$5); print $5}')

echo "$(date '+%Y-%m-%d %H:%M:%S') Utilizzo disco su ${CHECK_PATH}: ${USAGE_PERCENT}%"

if [[ "$USAGE_PERCENT" -ge "$THRESHOLD_PERCENT" ]]; then
    echo "SOGLIA SUPERATA (${USAGE_PERCENT}% >= ${THRESHOLD_PERCENT}%), invio avviso..."
    send_alert \
        "Attenzione: spazio disco al ${USAGE_PERCENT}% su $(hostname)" \
        "Il disco su ${CHECK_PATH} ($(hostname)) ha raggiunto il ${USAGE_PERCENT}% di utilizzo (soglia: ${THRESHOLD_PERCENT}%).

Dettaglio:
$(df -h "$CHECK_PATH")"
fi
