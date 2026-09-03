#!/usr/bin/env bash
# Verifica l'integrità dei sigilli prodotti da seal-admin-logs.sh per un
# tenant: ricalcola la hash chain dal manifest e, se disponibili, verifica
# le marche temporali RFC3161. Utile da mostrare in caso di verifica/audit.
#
# Uso: ./verify-admin-log-seal.sh nome-tenant
set -euo pipefail
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

TENANT="${1:?Uso: $0 nome-tenant}"
SEAL_DIR="${ADMIN_LOG_SEAL_DIR:-/mnt/graylog-data/admin-log-seals}/${TENANT}"
MANIFEST="${SEAL_DIR}/manifest.log"

[[ -f "$MANIFEST" ]] || { echo "Nessun manifest trovato per '${TENANT}' in ${SEAL_DIR}" >&2; exit 1; }

PREV_HASH="0000000000000000000000000000000000000000000000000000000000000000"
ERRORS=0
LINES=0

while IFS= read -r line; do
  LINES=$((LINES + 1))
  date_field=$(echo "$line" | grep -o 'date=[^ ]*' | cut -d= -f2)
  file_field=$(echo "$line" | grep -o 'file=[^ ]*' | cut -d= -f2)
  recorded_sha=$(echo "$line" | grep -o 'sha256=[^ ]*' | cut -d= -f2)
  recorded_chain=$(echo "$line" | grep -o 'chain_hash=[^ ]*' | cut -d= -f2)

  EXPORT_FILE="${SEAL_DIR}/${file_field}"
  if [[ ! -f "$EXPORT_FILE" ]]; then
    echo "[ERRORE] ${date_field}: file mancante (${file_field}) — possibile cancellazione." >&2
    ERRORS=$((ERRORS + 1))
    continue
  fi

  ACTUAL_SHA=$(sha256sum "$EXPORT_FILE" | cut -d' ' -f1)
  if [[ "$ACTUAL_SHA" != "$recorded_sha" ]]; then
    echo "[ERRORE] ${date_field}: hash del file NON corrisponde — contenuto alterato dopo il sigillo." >&2
    ERRORS=$((ERRORS + 1))
    continue
  fi

  EXPECTED_CHAIN=$(printf '%s%s' "$PREV_HASH" "$ACTUAL_SHA" | sha256sum | cut -d' ' -f1)
  if [[ "$EXPECTED_CHAIN" != "$recorded_chain" ]]; then
    echo "[ERRORE] ${date_field}: hash di catena NON corrisponde — sequenza compromessa o manifest alterato." >&2
    ERRORS=$((ERRORS + 1))
    continue
  fi

  echo "[OK] ${date_field}: file e catena integri."
  PREV_HASH="$recorded_chain"

  TSR_FILE="${SEAL_DIR}/${date_field}.tsr"
  TSQ_FILE="${SEAL_DIR}/${date_field}.tsq"
  if [[ -f "$TSR_FILE" && -f "$TSQ_FILE" ]]; then
    if openssl ts -reply -in "$TSR_FILE" -queryfile "$TSQ_FILE" -text >/dev/null 2>&1; then
      echo "      marca temporale presente e leggibile (verifica crittografica completa richiede il certificato della TSA)."
    else
      echo "      [ATTENZIONE] marca temporale presente ma non verificabile con openssl ts." >&2
    fi
  fi
done < "$MANIFEST"

echo
echo "Verificate ${LINES} voci, ${ERRORS} errori."
[[ "$ERRORS" -eq 0 ]] && echo "Catena integra dall'inizio alla fine." || exit 1
