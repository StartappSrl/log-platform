"""
Modulo condiviso: costruzione messaggi GELF e connessione mTLS a Graylog.
Usato sia da agent.py (Linux) sia da agent_windows.py (Windows).
"""
import json
import socket
import ssl
import time


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
    return json.dumps(msg).encode("utf-8") + b"\x00"


def connect(host: str, port: int, ca_cert: str, client_cert: str, client_key: str,
            timeout: int = 10) -> ssl.SSLSocket:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(ca_cert)
    context.load_cert_chain(certfile=client_cert, keyfile=client_key)
    raw_sock = socket.create_connection((host, port), timeout=timeout)
    return context.wrap_socket(raw_sock, server_hostname=host)
