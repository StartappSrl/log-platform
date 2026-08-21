#!/usr/bin/env python3
"""
Agent per l'invio di log al server Graylog via GELF TCP + TLS mutuo (mTLS).

Configurazione: agent.ini nella stessa cartella (vedi agent.ini.example).
Ogni messaggio include il campo "tenant" con l'identificativo del cliente,
usato lato Graylog per instradare il messaggio nello stream/index del
tenant corretto (vedi scripts/provision-tenant.sh).

Uso tipico: seguire (tail -f) uno o più file di log e inoltrarli.
    python3 agent.py --config agent.ini
"""
import argparse
import configparser
import json
import socket
import ssl
import time
from pathlib import Path


def build_gelf_message(tenant: str, host: str, short_message: str, full_message: str = "",
                        level: int = 6, extra: dict | None = None) -> bytes:
    msg = {
        "version": "1.1",
        "host": host,
        "short_message": short_message[:1000],
        "full_message": full_message or short_message,
        "timestamp": time.time(),
        "level": level,
        "_tenant": tenant,
    }
    if extra:
        for k, v in extra.items():
            msg[f"_{k}"] = v
    # GELF su TCP richiede il byte NUL come delimitatore di fine messaggio
    return json.dumps(msg).encode("utf-8") + b"\x00"


def connect(host: str, port: int, ca_cert: str, client_cert: str, client_key: str) -> ssl.SSLSocket:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(ca_cert)
    context.load_cert_chain(certfile=client_cert, keyfile=client_key)
    raw_sock = socket.create_connection((host, port), timeout=10)
    return context.wrap_socket(raw_sock, server_hostname=host)


def tail_file(path: Path):
    with open(path, "r", errors="replace") as f:
        f.seek(0, 2)  # vai alla fine, invia solo le righe nuove
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield line.rstrip("\n")


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

    if not log_files:
        raise SystemExit("Configura almeno un file in 'log_files' nell'agent.ini")

    sock = connect(graylog_host, graylog_port, ca_cert, client_cert, client_key)

    import threading

    def follow(path_str):
        nonlocal sock
        path = Path(path_str)
        for line in tail_file(path):
            payload = build_gelf_message(tenant, hostname, line, extra={"source_file": str(path)})
            while True:
                try:
                    sock.sendall(payload)
                    break
                except (BrokenPipeError, OSError):
                    time.sleep(2)
                    sock = connect(graylog_host, graylog_port, ca_cert, client_cert, client_key)

    threads = [threading.Thread(target=follow, args=(p,), daemon=True) for p in log_files]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
