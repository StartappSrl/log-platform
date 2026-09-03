#!/usr/bin/env bash
# Genera una CA interna usata per firmare:
#  - il certificato server dell'input GELF TCP+TLS di Graylog
#  - i certificati client di ogni agent (mTLS: solo agent con certificato
#    firmato da questa CA possono inviare log)
#
# Da eseguire UNA SOLA VOLTA. Conserva ca/ca-key.pem in modo sicuro (offline
# se possibile): chi la possiede può emettere certificati validi per i client.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p ca
cd ca

if [[ -f ca-key.pem ]]; then
  echo "Una CA esiste già in ca/. Rimuovila manualmente se vuoi rigenerarla (invaliderai tutti i certificati emessi)."
  exit 1
fi

openssl genrsa -out ca-key.pem 4096
openssl req -x509 -new -nodes -key ca-key.pem -sha256 -days 3650 \
  -out ca.pem \
  -subj "/O=LogPlatform/CN=LogPlatform Internal CA"

# Certificato server per l'input GELF di Graylog (CN = hostname/IP interno
# raggiunto dagli agent, da adattare)
GRAYLOG_CN="${1:-graylog}"
openssl genrsa -out graylog-server-key.pem 2048
openssl req -new -key graylog-server-key.pem -out graylog-server.csr -subj "/O=LogPlatform/CN=${GRAYLOG_CN}"
openssl x509 -req -in graylog-server.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial \
  -out graylog-server-cert.pem -days 825 -sha256
rm -f graylog-server.csr

chmod 600 ca-key.pem graylog-server-key.pem
echo "CA generata in ca/. Certificato server Graylog: ca/graylog-server-cert.pem / ca/graylog-server-key.pem"
echo "Usa scripts/generate-agent.sh <tenant> per emettere certificati client."
