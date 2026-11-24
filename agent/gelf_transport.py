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


def send_with_reconnect(sock_holder: dict, payload: bytes, connect_args: tuple, should_stop,
                         first_delay: float = 2, max_delay: float = 60) -> bool:
    """Invia 'payload' sulla connessione condivisa in sock_holder["sock"].

    Se la connessione è caduta (o non c'è ancora: sock_holder["sock"] è None)
    si ricollega a Graylog e riprova, all'infinito, con attese crescenti fino a
    'max_delay' secondi, finché l'invio riesce o should_stop() diventa vero.
    Un Graylog irraggiungibile (riavvio, aggiornamento, rete) non deve mai
    fermare l'agent: prima un errore di connessione chiudeva il thread e i log
    smettevano di partire fino al riavvio del servizio.
    Ritorna True se il messaggio è stato inviato, False se l'agent si sta fermando.
    """
    delay = first_delay
    while True:
        sock = sock_holder.get("sock")
        if sock is not None:
            try:
                sock.sendall(payload)
                return True
            except OSError:  # comprende BrokenPipeError e ssl.SSLError
                pass
        waited = 0.0
        while waited < delay:
            if should_stop():
                return False
            time.sleep(0.5)
            waited += 0.5
        try:
            sock_holder["sock"] = connect(*connect_args)
            delay = first_delay
        except OSError:
            sock_holder["sock"] = None
            delay = min(delay * 2, max_delay)
