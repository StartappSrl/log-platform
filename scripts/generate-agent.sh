#!/usr/bin/env bash
# Emette un certificato client (mTLS) per un tenant e prepara un pacchetto
# pronto da installare sul suo endpoint (agent.py + config + certificati).
#
# Uso: ./generate-agent.sh nome-tenant [hostname_graylog_pubblico]
set -euo pipefail
cd "$(dirname "$0")/.."

TENANT="${1:?Uso: $0 nome-tenant [hostname-graylog]}"
GRAYLOG_HOST="${2:-$(grep -E '^PUBLIC_DOMAIN=' .env 2>/dev/null | cut -d= -f2)}"
GRAYLOG_HOST="${GRAYLOG_HOST:-logs.tuodominio.it}"

if [[ ! -f ca/ca.pem || ! -f ca/ca-key.pem ]]; then
  echo "Esegui prima ./scripts/generate-ca.sh" >&2
  exit 1
fi

OUT_DIR="dist/agent-${TENANT}"
mkdir -p "$OUT_DIR"

openssl genrsa -out "$OUT_DIR/${TENANT}-key.pem" 2048
openssl req -new -key "$OUT_DIR/${TENANT}-key.pem" -out "$OUT_DIR/${TENANT}.csr" \
  -subj "/O=LogPlatform/CN=${TENANT}"
openssl x509 -req -in "$OUT_DIR/${TENANT}.csr" -CA ca/ca.pem -CAkey ca/ca-key.pem -CAcreateserial \
  -out "$OUT_DIR/${TENANT}.pem" -days 825 -sha256
rm -f "$OUT_DIR/${TENANT}.csr"

cp ca/ca.pem "$OUT_DIR/ca.pem"
cp agent/agent.py "$OUT_DIR/agent.py"

cat > "$OUT_DIR/agent.ini" <<EOF
[agent]
tenant = ${TENANT}
hostname = CAMBIA-QUESTO-HOSTNAME
graylog_host = ${GRAYLOG_HOST}
graylog_port = 12201
ca_cert = ca.pem
client_cert = ${TENANT}.pem
client_key = ${TENANT}-key.pem
log_files = /var/log/syslog
EOF

TARBALL="dist/agent-${TENANT}.tar.gz"
tar -C dist -czf "$TARBALL" "agent-${TENANT}"

echo "Pacchetto pronto: ${TARBALL}"
echo "Contiene: agent.py, agent.ini (da rifinire), certificato client, CA."
echo "Sull'endpoint del cliente: estrai, modifica agent.ini (hostname, log_files), poi:"
echo "  python3 agent.py --config agent.ini"
