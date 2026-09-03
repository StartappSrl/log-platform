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
    # GELF su TCP richiede il byte NUL come delimitatore di fine messaggio
    return json.dumps(msg).encode("utf-8") + b"\x00"


def connect(host: str, port: int, ca_cert: str, client_cert: str, client_key: str,
            timeout: int = 10) -> ssl.SSLSocket:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(ca_cert)
    context.load_cert_chain(certfile=client_cert, keyfile=client_key)
    raw_sock = socket.create_connection((host, port), timeout=timeout)
    return context.wrap_socket(raw_sock, server_hostname=host)


def send_with_retry(sock_holder: dict, payload: bytes, connect_args: tuple, retry_seconds: int = 2):
    """sock_holder è un dict {'sock': ssl.SSLSocket} passato per riferimento,
    così la funzione può sostituire la connessione se cade, senza bisogno di
    variabili globali/nonlocal lato chiamante."""
    while True:
        try:
            sock_holder["sock"].sendall(payload)
            return
        except (BrokenPipeError, OSError):
            time.sleep(retry_seconds)
            sock_holder["sock"] = connect(*connect_args)
