#!/usr/bin/env python3
"""
Agent Linux per l'invio di log al server Graylog via GELF TCP + TLS mutuo (mTLS).

Configurazione: agent.ini nella stessa cartella (vedi agent.ini.example).
Ogni messaggio include il campo "tenant" con l'identificativo del cliente,
usato lato Graylog per instradare il messaggio nello stream/index del
tenant corretto (vedi scripts/provision-tenant.sh).

Due categorie di file:
  log_files       -> log generici (retention breve, quella del tenant)
  admin_log_files -> log di accesso degli amministratori di sistema
                     (es. /var/log/auth.log su Linux): vengono taggati con
                     _log_class=admin-access, così possono finire in uno
                     stream separato a conservazione lunga (vedi
                     scripts/provision-admin-log-stream.sh e
                     scripts/seal-admin-logs.sh), per la conformità al
                     Provvedimento Garante Privacy 27/11/2008 sugli
                     Amministratori di Sistema.

Uso tipico: seguire (tail -f) uno o più file di log e inoltrarli.
    python3 agent.py --config agent.ini

Per installarlo come servizio persistente, vedi logplatform-agent.service.example.
Per Windows, usa agent_windows.py invece di questo script.
"""
import argparse
import configparser
import socket
import threading
import time
from pathlib import Path

from gelf_transport import build_gelf_message, connect


def tail_file(path: Path):
    with open(path, "r", errors="replace") as f:
        f.seek(0, 2)  # vai alla fine, invia solo le righe nuove
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield line.rstrip("\n")


def follow(path_str: str, tenant: str, hostname: str, connect_args: tuple, sock_holder: dict,
           log_class: str | None = None):
    path = Path(path_str)
    extra = {"source_file": str(path)}
    if log_class:
        extra["log_class"] = log_class
    for line in tail_file(path):
        payload = build_gelf_message(tenant, hostname, line, extra=extra)
        while True:
            try:
                sock_holder["sock"].sendall(payload)
                break
            except (BrokenPipeError, OSError):
                time.sleep(2)
                sock_holder["sock"] = connect(*connect_args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="agent.ini")
    args = parser.parse_args()

    cfg = configparser.ConfigParser()
    cfg.read(args.config)
    s = cfg["agent"]

    tenant = s["tenant"]
    hostname = s.get("hostname", socket.gethostname())
    graylog_host = s["graylog_host"]
    graylog_port = s.getint("graylog_port", 12201)
    ca_cert = s["ca_cert"]
    client_cert = s["client_cert"]
    client_key = s["client_key"]
    log_files = [p.strip() for p in s.get("log_files", "").split(",") if p.strip()]
    admin_log_files = [p.strip() for p in s.get("admin_log_files", "").split(",") if p.strip()]

    if not log_files and not admin_log_files:
        raise SystemExit("Configura almeno un file in 'log_files' o 'admin_log_files' nell'agent.ini")

    connect_args = (graylog_host, graylog_port, ca_cert, client_cert, client_key)
    sock_holder = {"sock": connect(*connect_args)}

    threads = []
    for p in log_files:
        threads.append(threading.Thread(
            target=follow, args=(p, tenant, hostname, connect_args, sock_holder, None), daemon=True))
    for p in admin_log_files:
        threads.append(threading.Thread(
            target=follow, args=(p, tenant, hostname, connect_args, sock_holder, "admin-access"), daemon=True))

    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
