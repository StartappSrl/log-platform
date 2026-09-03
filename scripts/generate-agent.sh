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
cp agent/agent_windows.py "$OUT_DIR/agent_windows.py"
cp agent/gelf_transport.py "$OUT_DIR/gelf_transport.py"
cp agent/requirements-windows.txt "$OUT_DIR/requirements-windows.txt"
cp agent/logplatform-agent.service.example "$OUT_DIR/logplatform-agent.service.example"

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
# event_logs = Application, System   # solo Windows, vedi agent_windows.py
EOF

TARBALL="dist/agent-${TENANT}.tar.gz"
tar -C dist -czf "$TARBALL" "agent-${TENANT}"

echo "Pacchetto pronto: ${TARBALL}"
echo "Contiene: agent.py (Linux) + agent_windows.py (Windows) + gelf_transport.py,"
echo "agent.ini (da rifinire), certificato client, CA, unit systemd di esempio."
echo
echo "--- Linux ---"
echo "  cd agent-${TENANT} && python3 agent.py --config agent.ini     (test manuale)"
echo "  Poi per farlo girare come servizio: adatta e installa"
echo "  logplatform-agent.service.example in /etc/systemd/system/"
echo
echo "--- Windows (da prompt Amministratore) ---"
echo "  pip install -r requirements-windows.txt"
echo "  python agent_windows.py debug      (test in primo piano)"
echo "  python agent_windows.py install"
echo "  python agent_windows.py start"
