#!/usr/bin/env bash
# Crea, per un tenant già provisionato (provision-tenant.sh), uno stream e
# un index set SEPARATO e dedicato ai log di accesso degli amministratori
# di sistema (log_class=admin-access), con retention lunga (default 200
# giorni, oltre il minimo di 6 mesi richiesto dal Provvedimento Garante
# Privacy 27/11/2008 sugli Amministratori di Sistema).
#
# Questo stream NON sostituisce la conservazione: va usato insieme a
# scripts/seal-admin-logs.sh (hash chain + marca temporale) per garantire
# anche la non alterabilità richiesta dalla normativa, non solo la durata.
#
# Uso: ./provision-admin-log-stream.sh nome-tenant [retention_giorni]
set -euo pipefail
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

TENANT="${1:?Uso: $0 nome-tenant [retention_giorni]}"
RETENTION_DAYS="${2:-${ADMIN_LOG_RETENTION_DAYS:-200}}"
GRAYLOG_URL="${GRAYLOG_URL:-http://localhost:9000}"
GRAYLOG_API_USER="${GRAYLOG_API_USER:-admin}"
if [[ -z "${GRAYLOG_API_PASSWORD:-}" ]]; then
  read -rsp "Password admin Graylog: " GRAYLOG_API_PASSWORD
  echo
fi

if (( RETENTION_DAYS < 180 )); then
  echo "ATTENZIONE: ${RETENTION_DAYS} giorni sono meno dei 6 mesi minimi richiesti." >&2
  echo "Procedo comunque perché l'hai chiesto esplicitamente, ma verificalo." >&2
fi

AUTH=(-u "${GRAYLOG_API_USER}:${GRAYLOG_API_PASSWORD}")
HDRS=(-H "Content-Type: application/json" -H "X-Requested-By: cli")

echo "Creo index set 'admin-access' per tenant '${TENANT}' (retention: ${RETENTION_DAYS} giorni)..."
INDEXSET_RESP=$(curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/system/indices/index_sets" -d "{
  \"title\": \"tenant-${TENANT}-admin-access\",
  \"description\": \"Log di accesso amministratori di sistema - tenant ${TENANT} (conservazione a norma)\",
  \"index_prefix\": \"tenant-${TENANT}-admin-access\",
  \"rotation_strategy_class\": \"org.graylog2.indexer.rotation.strategies.TimeBasedRotationStrategy\",
  \"rotation_strategy\": {\"type\": \"org.graylog2.indexer.rotation.strategies.TimeBasedRotationStrategyConfig\", \"rotation_period\": \"P1D\"},
  \"retention_strategy_class\": \"org.graylog2.indexer.retention.strategies.DeletionRetentionStrategy\",
  \"retention_strategy\": {\"type\": \"org.graylog2.indexer.retention.strategies.DeletionRetentionStrategyConfig\", \"max_number_of_indices\": ${RETENTION_DAYS}},
  \"index_analyzer\": \"standard\",
  \"shards\": 1,
  \"replicas\": 0,
  \"index_optimization_max_num_segments\": 1,
  \"index_optimization_disabled\": false
}")
INDEXSET_ID=$(echo "$INDEXSET_RESP" | grep -o '"id":"[a-f0-9]*"' | head -1 | cut -d'"' -f4)
echo "Index set id: ${INDEXSET_ID}"

echo "Creo stream 'admin-access' per tenant '${TENANT}'..."
STREAM_RESP=$(curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/streams" -d "{
  \"title\": \"tenant-${TENANT}-admin-access\",
  \"description\": \"Accessi amministratori di sistema - tenant ${TENANT}\",
  \"index_set_id\": \"${INDEXSET_ID}\",
  \"matching_type\": \"AND\",
  \"remove_matches_from_default_stream\": false
}")
STREAM_ID=$(echo "$STREAM_RESP" | grep -o '"stream_id":"[a-f0-9]*"' | head -1 | cut -d'"' -f4)
echo "Stream id: ${STREAM_ID}"

echo "Aggiungo regole di instradamento (tenant == ${TENANT} AND log_class == admin-access)..."
curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/streams/${STREAM_ID}/rules" -d "{
  \"field\": \"tenant\", \"type\": 1, \"value\": \"${TENANT}\", \"inverted\": false
}"
curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/streams/${STREAM_ID}/rules" -d "{
  \"field\": \"log_class\", \"type\": 1, \"value\": \"admin-access\", \"inverted\": false
}"

echo "Attivo lo stream..."
curl -sf "${AUTH[@]}" "${HDRS[@]}" -X POST "${GRAYLOG_URL}/api/streams/${STREAM_ID}/resume"

echo
echo "Fatto. Stream admin-access per '${TENANT}': ${STREAM_ID} (index set ${INDEXSET_ID})"
echo "Salva l'INDEX PREFIX 'tenant-${TENANT}-admin-access' per configurare"
echo "scripts/seal-admin-logs.sh (che sigilla quotidianamente i log di questo stream)."
echo
echo "Assicurati che gli agent dei sistemi di '${TENANT}' inviino i log di accesso"
echo "(auth.log, Security event log, ecc.) valorizzando 'admin_log_files' o"
echo "'admin_event_logs' in agent.ini, non solo 'log_files' generico."
