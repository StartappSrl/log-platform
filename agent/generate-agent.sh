#!/usr/bin/env bash
# Emette un certificato client (mTLS) per un tenant e prepara un pacchetto
# pronto da installare sul suo endpoint: agent Linux, Windows, e variante
# containerizzata per Synology/QNAP, tutti con supporto per l'archiviazione
# locale compressa e sigillata (oltre all'invio a Graylog).
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

# --- Certificato client (mTLS), comune a tutte le piattaforme ---
openssl genrsa -out "$OUT_DIR/${TENANT}-key.pem" 2048
openssl req -new -key "$OUT_DIR/${TENANT}-key.pem" -out "$OUT_DIR/${TENANT}.csr" \
  -subj "/O=LogPlatform/CN=${TENANT}"
openssl x509 -req -in "$OUT_DIR/${TENANT}.csr" -CA ca/ca.pem -CAkey ca/ca-key.pem -CAcreateserial \
  -out "$OUT_DIR/${TENANT}.pem" -days 825 -sha256
rm -f "$OUT_DIR/${TENANT}.csr"
cp ca/ca.pem "$OUT_DIR/ca.pem"

# --- File comuni a Linux e Windows (condividono gelf_transport.py e local_archive.py) ---
cp agent/agent.py "$OUT_DIR/agent.py"
cp agent/agent_windows.py "$OUT_DIR/agent_windows.py"
cp agent/gelf_transport.py "$OUT_DIR/gelf_transport.py"
cp agent/local_archive.py "$OUT_DIR/local_archive.py"
cp agent/inventory.py "$OUT_DIR/inventory.py"
cp agent/requirements-windows.txt "$OUT_DIR/requirements-windows.txt"
cp agent/logplatform-agent.service.example "$OUT_DIR/logplatform-agent.service.example"

# --- Linux: config con percorsi host reali ---
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
# admin_log_files = /var/log/auth.log      # log di accesso, conservazione a norma
# event_logs = Application, System         # solo Windows
# admin_event_logs = Security               # solo Windows

# Archiviazione locale compressa e sigillata (opzionale). Vuoto = disattivata.
local_archive_dir = /var/lib/logplatform-agent/archive
# local_archive_tsa_url =
# local_archive_tsa_user =
# local_archive_tsa_password =
EOF

# --- Variante per il deploy containerizzato su NAS: percorsi come li vede
# il container, non l'host. ---
mkdir -p "$OUT_DIR/config-nas"
cp "$OUT_DIR/ca.pem" "$OUT_DIR/config-nas/ca.pem"
cp "$OUT_DIR/${TENANT}.pem" "$OUT_DIR/config-nas/${TENANT}.pem"
cp "$OUT_DIR/${TENANT}-key.pem" "$OUT_DIR/config-nas/${TENANT}-key.pem"
cp agent/Dockerfile "$OUT_DIR/Dockerfile"
cp agent/docker-compose.nas.yml.example "$OUT_DIR/docker-compose.nas.yml.example"

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

# Dentro il container, l'archivio va su un percorso montato come volume
# persistente (aggiungi un volume dedicato in docker-compose.nas.yml se
# vuoi che sopravviva ai riavvii del container).
local_archive_dir = /archive
EOF

TARBALL="dist/agent-${TENANT}.tar.gz"
tar -C dist -czf "$TARBALL" "agent-${TENANT}"

echo "Pacchetto pronto: ${TARBALL}"
echo "Contiene: agent.py (Linux) + agent_windows.py (Windows) + gelf_transport.py +"
echo "local_archive.py (archiviazione locale compressa/sigillata) + inventory.py"
echo "(inventario hardware/software/risorse), condivisi tra le piattaforme,"
echo "agent.ini (da rifinire), certificato client, CA, unit systemd di esempio,"
echo "e Dockerfile + config-nas/ per il deploy su Synology/QNAP."
echo
echo "--- Linux (server/host generico) ---"
echo "  cd agent-${TENANT} && python3 agent.py --config agent.ini     (test manuale)"
echo "  Poi per farlo girare come servizio: adatta e installa"
echo "  logplatform-agent.service.example in /etc/systemd/system/"
echo "  (crea prima la cartella di archiviazione: sudo mkdir -p /var/lib/logplatform-agent/archive)"
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
