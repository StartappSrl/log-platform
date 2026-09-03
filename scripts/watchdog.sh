#!/usr/bin/env bash
# Controlla lo stato dei container e prova a riavviare quelli non sani.
# Se un servizio continua a fallire dopo il riavvio, invia una notifica
# a WATCHDOG_ALERT_WEBHOOK (se configurato in .env).
#
# Pensato per cron ogni 5 minuti:
#   */5 * * * * /percorso/final-package/scripts/watchdog.sh >> /var/log/logplatform-watchdog.log 2>&1
set -uo pipefail
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

SERVICES="mongodb opensearch graylog mariadb auth-service ai-service nginx"

notify() {
  local msg="$1"
  echo "[$(date -Iseconds)] $msg"
  if [[ -n "${WATCHDOG_ALERT_WEBHOOK:-}" ]]; then
    curl -sf -X POST -H "Content-Type: application/json" \
      -d "{\"text\": \"[Log Platform watchdog] ${msg}\"}" \
      "$WATCHDOG_ALERT_WEBHOOK" >/dev/null 2>&1 || true
  fi
}

for svc in $SERVICES; do
  STATE=$(docker compose ps -q "$svc" | xargs -r docker inspect -f '{{.State.Status}}' 2>/dev/null)
  if [[ -z "$STATE" ]]; then
    notify "Servizio '$svc' non trovato/non avviato. Provo a lanciarlo."
    docker compose up -d "$svc"
    continue
  fi
  if [[ "$STATE" != "running" ]]; then
    notify "Servizio '$svc' in stato '$STATE'. Riavvio in corso."
    docker compose restart "$svc"
    sleep 10
    NEW_STATE=$(docker compose ps -q "$svc" | xargs -r docker inspect -f '{{.State.Status}}' 2>/dev/null)
    if [[ "$NEW_STATE" != "running" ]]; then
      notify "Servizio '$svc' NON è ripartito correttamente (stato: $NEW_STATE). Serve intervento manuale."
    fi
  fi
done
