#!/usr/bin/env python3
# Azione NS8 aggiuntiva (non fa parte di configure-module): riceve un
# certificato server + CA (emessi dalla CA interna di auth-service, vedi
# ns8-auth/README-ca-e-agent.md) e configura l'input GELF TCP+TLS di
# Graylog per accettare connessioni mTLS dagli agent client.
#
# Input atteso:
#   {"ca_pem": "...", "server_cert_pem": "...", "server_key_pem": "...",
#    "graylog_api_user": "admin", "graylog_api_password": "..."}
#
# Uso:
#   api-cli run module/<istanza-graylog>/configure-gelf-input --data '{...}'
#
# NOTA: la porta GELF (12201) viene pubblicata direttamente su 0.0.0.0 nel
# systemd unit (non passa dalla porta dinamica TCP_PORT di NS8, perché deve
# restare stabile: gli agent client la usano fissa). Serve APRIRE
# ESPLICITAMENTE la porta 12201/tcp nel firewall del nodo NS8 - non lo fa
# questo script. Verifica con la gestione firewall di NS8/firewalld.
import json
import os
import sys
import requests

data = json.load(sys.stdin)
ca_pem = data.get("ca_pem")
server_cert_pem = data.get("server_cert_pem")
server_key_pem = data.get("server_key_pem")
graylog_api_user = data.get("graylog_api_user")
graylog_api_password = data.get("graylog_api_password")

for required_name, value in (
    ("ca_pem", ca_pem), ("server_cert_pem", server_cert_pem), ("server_key_pem", server_key_pem),
    ("graylog_api_user", graylog_api_user), ("graylog_api_password", graylog_api_password),
):
    if not value:
        print(f"{required_name} mancante", file=sys.stderr)
        sys.exit(1)

# Scrive i certificati in state/certs/ (percorso relativo = dentro %E/state/
# per le azioni NS8, stesso pattern gia' usato da 10create_secrets in altri
# moduli). Il systemd unit di graylog monta questa cartella su
# /etc/graylog/certs dentro il container (vedi README-gelf-input.md).
os.makedirs("certs", exist_ok=True)
with open("certs/ca.pem", "w") as f:
    f.write(ca_pem)
with open("certs/server.pem", "w") as f:
    f.write(server_cert_pem)
with open("certs/server-key.pem", "w") as f:
    f.write(server_key_pem)
os.chmod("certs/server-key.pem", 0o600)

graylog_url = f"http://127.0.0.1:{os.environ['TCP_PORT']}"
resp = requests.post(
    f"{graylog_url}/api/system/inputs",
    auth=(graylog_api_user, graylog_api_password),
    headers={"Content-Type": "application/json", "X-Requested-By": "cli"},
    json={
        "title": "GELF TCP+TLS mTLS (agent)",
        "type": "org.graylog2.inputs.gelf.tcp.GELFTCPInput",
        "global": True,
        "configuration": {
            "bind_address": "0.0.0.0",
            "port": 12201,
            "tls_enable": True,
            "tls_cert_file": "/etc/graylog/certs/server.pem",
            "tls_key_file": "/etc/graylog/certs/server-key.pem",
            "tls_client_auth": "required",
            "tls_client_auth_cert_file": "/etc/graylog/certs/ca.pem",
            "max_message_size": 2097152,
            "tcp_keepalive": True,
        },
    },
    timeout=15,
)
if not resp.ok:
    print(f"Errore creazione input Graylog: {resp.status_code} {resp.text}", file=sys.stderr)
    sys.exit(1)

print("Input GELF TCP+TLS creato con successo.")
print("IMPORTANTE: apri la porta 12201/tcp nel firewall del nodo se non gia' fatto.")
