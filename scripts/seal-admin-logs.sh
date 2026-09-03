#!/usr/bin/env bash
# Sigilla quotidianamente i log di accesso amministratori di un tenant, per
# soddisfare il requisito di NON ALTERABILITÀ del Provvedimento Garante
# Privacy 27/11/2008 (la retention lunga da sola, vedi
# provision-admin-log-stream.sh, copre solo la DURATA, non l'integrità).
#
# Meccanismo:
#  1. Esporta da OpenSearch tutti i documenti dell'indice del giorno
#     precedente per lo stream admin-access del tenant.
#  2. Calcola lo SHA-256 dell'export.
#  3. Concatena all'hash del giorno precedente (hash chain): se un giorno
#     qualunque viene alterato a posteriori, la catena si rompe e si vede.
#  4. Se configurata una TSA (Time Stamping Authority, RFC 3161), chiede una
#     marca temporale sull'hash della catena: prova indipendente, verificabile
#     da terzi, che quell'hash esisteva già a quella data.
#  5. Rende i file immutabili a livello filesystem (chattr +i, se disponibile).
#
# LIMITE ONESTO: chattr +i può essere rimosso da chi ha accesso root alla
# macchina — non è WORM hardware. La protezione reale contro un
# amministratore malintenzionato viene dalla combinazione hash-chain +
# marca temporale ESTERNA (fuori dal tuo controllo), non dal solo flag
# immutabile. Per un valore probatorio più forte, valuta una TSA qualificata
# a pagamento invece di quella di default (gratuita, best-effort).
#
# Pensato per cron giornaliero (dopo la mezzanotte, quando l'indice del
# giorno prima è "chiuso"):
#   15 1 * * * /percorso/scripts/seal-admin-logs.sh acme >> /var/log/logplatform-seal.log 2>&1
#
# Uso: ./seal-admin-logs.sh nome-tenant [data_YYYY.MM.DD]
set -euo pipefail
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

TENANT="${1:?Uso: $0 nome-tenant [data_YYYY.MM.DD]}"
TARGET_DATE="${2:-$(date -d 'yesterday' +%Y.%m.%d 2>/dev/null || date -v-1d +%Y.%m.%d)}"

OPENSEARCH_URL="${OPENSEARCH_URL:-http://localhost:9200}"
SEAL_DIR="${ADMIN_LOG_SEAL_DIR:-/mnt/graylog-data/admin-log-seals}/${TENANT}"
TSA_URL="${ADMIN_LOG_TSA_URL:-https://freetsa.org/tsr}"
INDEX_PATTERN="tenant-${TENANT}-admin-access_*"

mkdir -p "$SEAL_DIR"
CHAIN_FILE="${SEAL_DIR}/chain-state.txt"
MANIFEST="${SEAL_DIR}/manifest.log"   # append-only: mai riscritto, solo accodato

PREV_HASH=$(cat "$CHAIN_FILE" 2>/dev/null || echo "0000000000000000000000000000000000000000000000000000000000000000")

EXPORT_FILE="${SEAL_DIR}/${TARGET_DATE}.ndjson"
echo "Esporto i documenti admin-access del ${TARGET_DATE} per tenant '${TENANT}'..."

# Scroll semplice su tutti i documenti dell'indice del giorno (assume
# volumi giornalieri gestibili; per volumi molto grandi valuta lo Scroll
# API a paginazione multipla invece di size fisso).
curl -sf -X GET "${OPENSEARCH_URL}/${INDEX_PATTERN}/_search?size=10000" \
  -H "Content-Type: application/json" \
  -d '{"query": {"match_all": {}}, "sort": [{"timestamp": "asc"}]}' \
  > "$EXPORT_FILE"

if [[ ! -s "$EXPORT_FILE" ]]; then
  echo "ATTENZIONE: nessun dato esportato per ${TARGET_DATE} — indice assente o vuoto." >&2
  echo "Se ti aspettavi log quel giorno, questo è un possibile buco di completezza da indagare." >&2
fi

FILE_HASH=$(sha256sum "$EXPORT_FILE" | cut -d' ' -f1)
CHAIN_INPUT=$(printf '%s%s' "$PREV_HASH" "$FILE_HASH")
CHAIN_HASH=$(printf '%s' "$CHAIN_INPUT" | sha256sum | cut -d' ' -f1)

echo "$CHAIN_HASH" > "$CHAIN_FILE"

TSR_FILE=""
if command -v openssl >/dev/null && [[ -n "$TSA_URL" ]]; then
  TSQ_FILE="${SEAL_DIR}/${TARGET_DATE}.tsq"
  TSR_FILE="${SEAL_DIR}/${TARGET_DATE}.tsr"
  openssl ts -query -digest "$CHAIN_HASH" -sha256 -no_nonce -out "$TSQ_FILE" 2>/dev/null || true
  if [[ -f "$TSQ_FILE" ]]; then
    curl -sf -H "Content-Type: application/timestamp-query" --data-binary "@${TSQ_FILE}" "$TSA_URL" \
      -o "$TSR_FILE" 2>/dev/null || echo "ATTENZIONE: marca temporale non ottenuta (TSA non raggiungibile?)." >&2
  fi
fi

echo "$(date -Iseconds) tenant=${TENANT} date=${TARGET_DATE} file=$(basename "$EXPORT_FILE") sha256=${FILE_HASH} chain_hash=${CHAIN_HASH} tsr=$(basename "${TSR_FILE:-nessuna}")" >> "$MANIFEST"

# Rendi i file immutabili (best-effort: richiede filesystem che lo supporta,
# es. ext4, e permessi root). Non è un errore bloccante se fallisce.
if command -v chattr >/dev/null; then
  chattr +i "$EXPORT_FILE" 2>/dev/null || true
  [[ -n "$TSR_FILE" && -f "$TSR_FILE" ]] && chattr +i "$TSR_FILE" 2>/dev/null || true
fi

echo "Sigillo completato per ${TARGET_DATE}: ${EXPORT_FILE}"
echo "  sha256 file:  ${FILE_HASH}"
echo "  hash catena:  ${CHAIN_HASH}"
[[ -n "$TSR_FILE" && -f "$TSR_FILE" ]] && echo "  marca temporale: ${TSR_FILE}"
