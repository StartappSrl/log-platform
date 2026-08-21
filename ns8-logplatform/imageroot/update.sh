#!/usr/bin/env bash
# Lifecycle "update" del modulo NS8 logplatform — SCAFFOLD NON VALIDATO.
set -euo pipefail
DATA_DIR="${DATA_DIR:-/home/logplatform}"

MODULE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODULE_JSON="${MODULE_DIR}/module.json"

echo "Backup rapido prima dell'aggiornamento..."
"${DATA_DIR}/scripts/backup.sh" || echo "Backup fallito o non configurato: procedo comunque."

echo "Pull delle immagini aggiornate secondo module.json (nuovo tag)..."
for svc in auth-service ai-service graylog mongodb opensearch mariadb nginx; do
  image=$(jq -r --arg s "$svc" '.images[$s] // empty' "$MODULE_JSON" 2>/dev/null)
  [[ -n "$image" ]] && podman pull "$image"
done

echo "Riavvio i container per applicare le nuove immagini..."
systemctl --user restart 'logplatform-*.service' 2>/dev/null || true
