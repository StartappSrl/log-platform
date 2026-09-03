#!/usr/bin/env bash
# Collega gli allarmi di un tenant al triage AI: crea una notifica HTTP verso
# ai-service e una event definition di esempio (soglia messaggi) sullo stream
# del tenant. Adatta la condizione alle tue esigenze reali dalla UI Graylog.
#
# Uso: ./create-alert-notification.sh nome-tenant stream_id
set -euo pipefail
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

TENANT="${1:?Uso: $0 nome-tenant stream_id}"
STREAM_ID="${2:?Uso: $0 nome-tenant stream_id}"
GRAYLOG_URL="${GRAYLOG_URL:-http://localhost:9000}"
GRAYLOG_API_USER="${GRAYLOG_API_USER:-admin}"
if [[ -z "${GRAYLOG_API_PASSWORD:-}" ]]; then
  read -rsp "Password admin Graylog: " GRAYLOG_API_PASSWORD
  echo
fi
if [[ -z "${AI_SERVICE_WEBHOOK_TOKEN:-}" ]]; then
  echo "AI_SERVICE_WEBHOOK_TOKEN non trovato in .env" >&2
  exit 1
fi

AUTH=(-u "${GRAYLOG_API_USER}:${GRAYLOG_API_PASSWORD}")
HDRS=(-H "Content-Type: application/json" -H "X-Requested-By: cli")
AI_SERVICE_URL="${AI_SERVICE_URL:-http://ai-service:8100}"

echo "Creo notifica HTTP verso ai-service..."
NOTIF_RESP=$(curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/events/notifications" -d "{
  \"title\": \"AI triage - ${TENANT}\",
  \"description\": \"Invia l'allarme al servizio di triage AI\",
  \"config\": {
    \"type\": \"http-notification-v1\",
    \"url\": \"${AI_SERVICE_URL}/webhook/${TENANT}\",
    \"headers\": \"X-Webhook-Token: ${AI_SERVICE_WEBHOOK_TOKEN}\",
    \"api_key\": \"\",
    \"api_secret\": \"\",
    \"basic_auth\": \"\",
    \"skip_tls_verification\": false
  }
}")
NOTIF_ID=$(echo "$NOTIF_RESP" | grep -o '"id":"[a-f0-9]*"' | head -1 | cut -d'"' -f4)
echo "Notifica creata: ${NOTIF_ID}"

echo "Creo event definition di esempio (>=20 messaggi in 5 minuti sullo stream del tenant)..."
curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/events/definitions" -d "{
  \"title\": \"Picco messaggi - ${TENANT}\",
  \"description\": \"Esempio: adatta soglia/condizione dalla UI Graylog\",
  \"priority\": 2,
  \"alert\": true,
  \"config\": {
    \"type\": \"aggregation-v1\",
    \"query\": \"\",
    \"streams\": [\"${STREAM_ID}\"],
    \"group_by\": [],
    \"series\": [],
    \"conditions\": null,
    \"search_within_ms\": 300000,
    \"execute_every_ms\": 60000
  },
  \"field_spec\": {},
  \"key_spec\": [],
  \"notification_settings\": {\"grace_period_ms\": 300000, \"backlog_size\": 5},
  \"notifications\": [{\"notification_id\": \"${NOTIF_ID}\"}]
}"

echo
echo "Fatto. Vai nella UI Graylog (Alerts > Event Definitions) per rifinire la"
echo "condizione: quella creata qui è solo un placeholder di esempio."
