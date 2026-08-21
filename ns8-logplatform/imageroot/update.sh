#!/usr/bin/env bash
# Lifecycle "update" del modulo NS8 logplatform — SCAFFOLD NON VALIDATO.
set -euo pipefail
DATA_DIR="${DATA_DIR:-/home/logplatform}"

echo "Backup rapido prima dell'aggiornamento..."
"${DATA_DIR}/scripts/backup.sh" || echo "Backup fallito o non configurato: procedo comunque."

echo "Riavvio i container per applicare eventuali nuove immagini..."
systemctl --user restart 'logplatform-*.service' 2>/dev/null || true
