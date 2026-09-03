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
cp agent/Dockerfile "$OUT_DIR/Dockerfile"
cp agent/docker-compose.nas.yml.example "$OUT_DIR/docker-compose.nas.yml.example"

# Config dedicata al deploy containerizzato (Synology/QNAP): percorsi come
# li vede il container, non come li vede l'host.
mkdir -p "$OUT_DIR/config-nas"
cp "$OUT_DIR/ca.pem" "$OUT_DIR/config-nas/ca.pem"
cp "$OUT_DIR/${TENANT}.pem" "$OUT_DIR/config-nas/${TENANT}.pem"
cp "$OUT_DIR/${TENANT}-key.pem" "$OUT_DIR/config-nas/${TENANT}-key.pem"

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

# Variante per il deploy containerizzato su NAS: i percorsi puntano dentro
# il container (vedi docker-compose.nas.yml.example), non sull'host.
cat > "$OUT_DIR/config-nas/agent.ini" <<EOF
[agent]
tenant = ${TENANT}
hostname = CAMBIA-QUESTO-HOSTNAME
graylog_host = ${GRAYLOG_HOST}
graylog_port = 12201
ca_cert = /config/ca.pem
client_cert = /config/${TENANT}.pem
client_key = /config/${TENANT}-key.pem
log_files = /logs/system/syslog
EOF

TARBALL="dist/agent-${TENANT}.tar.gz"
tar -C dist -czf "$TARBALL" "agent-${TENANT}"

echo "Pacchetto pronto: ${TARBALL}"
echo "Contiene: agent.py (Linux) + agent_windows.py (Windows) + gelf_transport.py,"
echo "agent.ini (da rifinire), certificato client, CA, unit systemd di esempio,"
echo "e Dockerfile + config-nas/ per il deploy su Synology/QNAP."
echo
echo "--- Linux (server/host generico) ---"
echo "  cd agent-${TENANT} && python3 agent.py --config agent.ini     (test manuale)"
echo "  Poi per farlo girare come servizio: adatta e installa"
echo "  logplatform-agent.service.example in /etc/systemd/system/"
echo
echo "--- Windows (da prompt Amministratore) ---"
echo "  pip install -r requirements-windows.txt"
echo "  python agent_windows.py debug      (test in primo piano)"
echo "  python agent_windows.py install"
echo "  python agent_windows.py start"
echo
echo "--- Synology (Container Manager) / QNAP (Container Station) ---"
echo "  1. Copia la cartella agent-${TENANT}/ sul NAS (es. via File Station/condivisione)"
echo "  2. Rinomina: mv config-nas config   (o modifica il volume in docker-compose.nas.yml.example)"
echo "  3. Adatta docker-compose.nas.yml.example ai percorsi di log reali del tuo NAS,"
echo "     poi rinominalo in docker-compose.yml"
echo "  4. Synology: Container Manager > Progetto > Crea, incolla il YAML"
echo "     QNAP: Container Station > Crea > Applicazione > Crea da YAML"
