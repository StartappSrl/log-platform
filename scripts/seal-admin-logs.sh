#!/usr/bin/env bash
# Sigilla (esporta + comprime + hash chain + marca temporale) i log di
# accesso amministratori (log_class=admin-access) di TUTTI i tenant in
# un'unica operazione centralizzata - una sola marca temporale invece di
# una per ogni singolo endpoint/cliente.
#
# Pensato per girare via cron DIRETTAMENTE SUL NODO NS8 (non dentro un
# container), con accesso a Graylog sulla sua porta locale.
#
# Per la sigillatura quindicinale (2 volte al mese, invece che ogni
# giorno): pianifica via cron il giorno 1 e il giorno 16 di ogni mese,
# con SEAL_RANGE_HOURS=360 (15 giorni) - vedi il crontab di esempio in
# fondo a questo file.
#
# Configurazione (variabili d'ambiente, es. da un file sourcing prima
# della chiamata):
#   GRAYLOG_URL              es. http://127.0.0.1:20004
#   GRAYLOG_API_USER
#   GRAYLOG_API_PASSWORD
#   SEAL_STATE_DIR           default /var/lib/logplatform-seal
#   SEAL_RANGE_HOURS         default 24 (ore indietro da esportare)
#   ADMIN_LOG_TSA_URL        se assente, sigilla solo con hash chain
#   ADMIN_LOG_TSA_USER / ADMIN_LOG_TSA_PASSWORD
#   ADMIN_LOG_TSA_CLIENT_CERT / ADMIN_LOG_TSA_CLIENT_KEY   (alternativa)
#
# Limite onesto, identico a quello del meccanismo lato agent: la
# sigillatura rende evidente una manomissione A POSTERIORI (rompe la
# catena da quel punto in poi), ma non e' WORM hardware - chi ha accesso
# pieno a Graylog/OpenSearch nella finestra PRIMA della sigillatura
# potrebbe comunque alterare i dati senza che questo controllo se ne
# accorga in quel preciso momento.
set -euo pipefail

STATE_DIR="${SEAL_STATE_DIR:-/var/lib/logplatform-seal}"
mkdir -p "$STATE_DIR"

GRAYLOG_URL="${GRAYLOG_URL:?serve GRAYLOG_URL, es. http://127.0.0.1:20004}"
GRAYLOG_API_USER="${GRAYLOG_API_USER:?serve GRAYLOG_API_USER}"
GRAYLOG_API_PASSWORD="${GRAYLOG_API_PASSWORD:?serve GRAYLOG_API_PASSWORD}"
RANGE_HOURS="${SEAL_RANGE_HOURS:-24}"

RUN_ID="$(date -u +%Y-%m-%dT%H%M%SZ)"
EXPORT_FILE="$STATE_DIR/${RUN_ID}.ndjson"
GZ_FILE="$STATE_DIR/${RUN_ID}.ndjson.gz"
CHAIN_FILE="$STATE_DIR/chain-state.txt"
MANIFEST_FILE="$STATE_DIR/manifest.jsonl"

echo "Esporto i messaggi admin-access delle ultime $RANGE_HOURS ore (tutti i tenant)..."

curl -s -u "${GRAYLOG_API_USER}:${GRAYLOG_API_PASSWORD}" \
  -H "Accept: application/json" \
  "${GRAYLOG_URL}/api/search/universal/relative?query=log_class:admin-access&range=$((RANGE_HOURS * 3600))&limit=10000&fields=timestamp,message,full_message,source,tenant" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for entry in data.get('messages', []):
    m = entry.get('message', entry)
    print(json.dumps(m, ensure_ascii=False))
" > "$EXPORT_FILE"

LINE_COUNT=$(wc -l < "$EXPORT_FILE")
echo "Esportate $LINE_COUNT righe."

if [[ "$LINE_COUNT" -eq 0 ]]; then
  echo "Nessun log admin-access nel periodo: niente da sigillare in questa esecuzione."
  rm -f "$EXPORT_FILE"
  exit 0
fi

gzip -f "$EXPORT_FILE"

FILE_HASH=$(sha256sum "$GZ_FILE" | cut -d' ' -f1)

if [[ -f "$CHAIN_FILE" ]]; then
  PREV_HASH=$(cat "$CHAIN_FILE")
else
  PREV_HASH=$(printf '0%.0s' {1..64})
fi

CHAIN_HASH=$(echo -n "${PREV_HASH}${FILE_HASH}" | sha256sum | cut -d' ' -f1)
echo "$CHAIN_HASH" > "$CHAIN_FILE"

TSR_FILE=""
if [[ -n "${ADMIN_LOG_TSA_URL:-}" ]]; then
  echo "Richiedo la marca temporale a ${ADMIN_LOG_TSA_URL}..."
  TSQ_FILE="$STATE_DIR/${RUN_ID}.tsq"
  TSR_FILE="$STATE_DIR/${RUN_ID}.tsr"

  openssl ts -query -digest "$CHAIN_HASH" -sha256 -no_nonce -out "$TSQ_FILE"

  CURL_AUTH_ARGS=()
  if [[ -n "${ADMIN_LOG_TSA_USER:-}" ]]; then
    CURL_AUTH_ARGS+=(-u "${ADMIN_LOG_TSA_USER}:${ADMIN_LOG_TSA_PASSWORD:-}")
  fi
  if [[ -n "${ADMIN_LOG_TSA_CLIENT_CERT:-}" ]]; then
    CURL_AUTH_ARGS+=(--cert "${ADMIN_LOG_TSA_CLIENT_CERT}" --key "${ADMIN_LOG_TSA_CLIENT_KEY}")
  fi

  if curl -s -f "${CURL_AUTH_ARGS[@]}" \
      -H "Content-Type: application/timestamp-query" \
      --data-binary "@${TSQ_FILE}" \
      -o "$TSR_FILE" \
      "${ADMIN_LOG_TSA_URL}"; then
    echo "Marca temporale ottenuta: $TSR_FILE"
  else
    echo "ATTENZIONE: richiesta marca temporale fallita - la sigillatura procede comunque senza (hash chain valida lo stesso, solo senza timbro esterno)." >&2
    rm -f "$TSR_FILE"
    TSR_FILE=""
  fi
else
  echo "ADMIN_LOG_TSA_URL non configurata: sigillo solo con hash chain, senza marca temporale esterna."
fi

TSR_BASENAME=""
if [[ -n "$TSR_FILE" && -f "$TSR_FILE" ]]; then
  TSR_BASENAME="$(basename "$TSR_FILE")"
fi

python3 -c "
import json
entry = {
    'sealed_at_utc': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
    'period_covered_hours': $RANGE_HOURS,
    'run_id': '$RUN_ID',
    'file': '$(basename "$GZ_FILE")',
    'lines': $LINE_COUNT,
    'sha256': '$FILE_HASH',
    'chain_hash': '$CHAIN_HASH',
    'tsr': '$TSR_BASENAME' or None,
}
print(json.dumps(entry))
" >> "$MANIFEST_FILE"

echo "Sigillatura completata: $GZ_FILE ($LINE_COUNT righe, hash catena $CHAIN_HASH)"

# --- Esempio di pianificazione via cron, due volte al mese ---
# Aggiungi con: crontab -e (come root, sul nodo)
#
# 0 3 1,16 * * GRAYLOG_URL=http://127.0.0.1:20004 GRAYLOG_API_USER=admin \
#   GRAYLOG_API_PASSWORD='...' SEAL_RANGE_HOURS=360 \
#   ADMIN_LOG_TSA_URL=https://tsa.namirial.it/... \
#   /root/logmanager/final-package/scripts/seal-admin-logs.sh >> /var/log/logplatform-seal.log 2>&1
