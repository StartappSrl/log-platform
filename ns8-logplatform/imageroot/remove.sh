#!/usr/bin/env bash
# Lifecycle "remove" del modulo NS8 logplatform — SCAFFOLD NON VALIDATO.
set -euo pipefail
DATA_DIR="${DATA_DIR:-/home/logplatform}"

echo "Fermo e rimuovo le unit systemd del modulo..."
systemctl --user stop 'logplatform-*.service' 2>/dev/null || true
systemctl --user disable 'logplatform-*.service' 2>/dev/null || true

read -rp "Eliminare anche i dati in ${DATA_DIR}? (scrivi SI per confermare): " CONFIRM
if [[ "$CONFIRM" == "SI" ]]; then
  rm -rf "$DATA_DIR"
  echo "Dati rimossi."
else
  echo "Dati conservati in ${DATA_DIR}."
fi
