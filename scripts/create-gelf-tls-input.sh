#!/usr/bin/env bash
# Crea (via API REST di Graylog) un input GELF TCP con TLS mutuo (mTLS),
# usando il certificato server e la CA generati da generate-ca.sh.
#
# Richiede: GRAYLOG_API_USER e GRAYLOG_API_PASSWORD nell'ambiente (o passati
# come variabili), oppure verranno chieste interattivamente.
set -euo pipefail
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

GRAYLOG_URL="${GRAYLOG_URL:-http://localhost:9000}"
GRAYLOG_API_USER="${GRAYLOG_API_USER:-admin}"
if [[ -z "${GRAYLOG_API_PASSWORD:-}" ]]; then
  read -rsp "Password admin Graylog: " GRAYLOG_API_PASSWORD
  echo
fi

if [[ ! -f ca/graylog-server-cert.pem ]]; then
  echo "Esegui prima ./scripts/generate-ca.sh" >&2
  exit 1
fi

curl -sf -u "${GRAYLOG_API_USER}:${GRAYLOG_API_PASSWORD}" \
  -H "Content-Type: application/json" -H "X-Requested-By: cli" \
  -X POST "${GRAYLOG_URL}/api/system/inputs" \
  -d '{
    "title": "GELF TCP+TLS mTLS (agent)",
    "type": "org.graylog2.inputs.gelf.tcp.GELFTCPInput",
    "global": true,
    "configuration": {
      "bind_address": "0.0.0.0",
      "port": 12201,
      "tls_enable": true,
      "tls_cert_file": "/etc/graylog/certs/graylog-server-cert.pem",
      "tls_key_file": "/etc/graylog/certs/graylog-server-key.pem",
      "tls_client_auth": "required",
      "tls_client_auth_cert_file": "/etc/graylog/certs/ca.pem",
      "max_message_size": 2097152,
      "tcp_keepalive": true
    }
  }' && echo "Input GELF TCP+TLS creato."
