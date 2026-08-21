#!/usr/bin/env bash
# Crea, per un nuovo tenant, un index set dedicato e uno stream che instrada
# automaticamente i messaggi con il campo "tenant" corrispondente (impostato
# dall'agent, vedi agent/agent.py).
#
# Uso: ./provision-tenant.sh nome-tenant
set -euo pipefail
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

TENANT="${1:?Uso: $0 nome-tenant}"
GRAYLOG_URL="${GRAYLOG_URL:-http://localhost:9000}"
GRAYLOG_API_USER="${GRAYLOG_API_USER:-admin}"
if [[ -z "${GRAYLOG_API_PASSWORD:-}" ]]; then
  read -rsp "Password admin Graylog: " GRAYLOG_API_PASSWORD
  echo
fi

AUTH=(-u "${GRAYLOG_API_USER}:${GRAYLOG_API_PASSWORD}")
HDRS=(-H "Content-Type: application/json" -H "X-Requested-By: cli")

echo "Creo index set per tenant '${TENANT}'..."
INDEXSET_RESP=$(curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/system/indices/index_sets" -d "{
  \"title\": \"tenant-${TENANT}\",
  \"description\": \"Log del tenant ${TENANT}\",
  \"index_prefix\": \"tenant-${TENANT}\",
  \"rotation_strategy_class\": \"org.graylog2.indexer.rotation.strategies.TimeBasedRotationStrategy\",
  \"rotation_strategy\": {\"type\": \"org.graylog2.indexer.rotation.strategies.TimeBasedRotationStrategyConfig\", \"rotation_period\": \"P1D\"},
  \"retention_strategy_class\": \"org.graylog2.indexer.retention.strategies.DeletionRetentionStrategy\",
  \"retention_strategy\": {\"type\": \"org.graylog2.indexer.retention.strategies.DeletionRetentionStrategyConfig\", \"max_number_of_indices\": 90},
  \"index_analyzer\": \"standard\",
  \"shards\": 1,
  \"replicas\": 0,
  \"index_optimization_max_num_segments\": 1,
  \"index_optimization_disabled\": false
}")
INDEXSET_ID=$(echo "$INDEXSET_RESP" | grep -o '"id":"[a-f0-9]*"' | head -1 | cut -d'"' -f4)
echo "Index set id: ${INDEXSET_ID}"

echo "Creo stream per tenant '${TENANT}'..."
STREAM_RESP=$(curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/streams" -d "{
  \"title\": \"tenant-${TENANT}\",
  \"description\": \"Stream log tenant ${TENANT}\",
  \"index_set_id\": \"${INDEXSET_ID}\",
  \"matching_type\": \"AND\",
  \"remove_matches_from_default_stream\": true
}")
STREAM_ID=$(echo "$STREAM_RESP" | grep -o '"stream_id":"[a-f0-9]*"' | head -1 | cut -d'"' -f4)
echo "Stream id: ${STREAM_ID}"

echo "Aggiungo regola di instradamento (tenant == ${TENANT})..."
curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/streams/${STREAM_ID}/rules" -d "{
  \"field\": \"tenant\",
  \"type\": 1,
  \"value\": \"${TENANT}\",
  \"inverted\": false
}"

echo "Attivo lo stream..."
curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/streams/${STREAM_ID}/resume"

echo
echo "Tenant '${TENANT}' provisionato. Stream ID: ${STREAM_ID} — Index set ID: ${INDEXSET_ID}"
echo "Salvali, ti servono per create-alert-notification.sh e per generate-agent.sh"
