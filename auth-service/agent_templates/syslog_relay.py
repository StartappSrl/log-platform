"""
Riceve syslog (UDP e/o TCP) sulla rete locale del cliente e lo inoltra
attraverso lo stesso canale sicuro (GELF su TLS con certificato client)
gia' usato per i file di log - il dispositivo che manda syslog non deve
mai raggiungere Internet direttamente, ne' avere le nostre credenziali:
parla solo con questo relay sulla rete locale.
"""
import socket
import threading
import time

from syslog_parser import parse_syslog_message

_shutdown = threading.Event()


def _forward(tenant: str, relay_hostname: str, raw_message: str, source_ip: str,
             build_gelf_message_fn, sock_holder: dict, archiver=None):
    parsed = parse_syslog_message(raw_message)
    # Se il dispositivo non ha detto il proprio hostname (nessun PRI/formato
    # riconosciuto), usiamo il suo IP sorgente come identificativo -
    # sempre meglio che perdere l'informazione di CHI ha mandato il log.
    device_hostname = parsed["hostname"] or source_ip
    extra = {"log_class": "syslog-relay", "syslog_relay_host": relay_hostname}

    if archiver:
        archiver.write_line(raw_message)

    payload = build_gelf_message_fn(tenant, device_hostname, parsed["message"],
                                      level=parsed["level"], extra=extra)
    while True:
        try:
            sock_holder["sock"].sendall(payload)
            return
        except (BrokenPipeError, OSError):
            if _shutdown.is_set():
                return
            time.sleep(2)


def _udp_listener(bind_host: str, port: int, tenant: str, relay_hostname: str,
                    build_gelf_message_fn, sock_holder: dict, archiver=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_host, port))
    sock.settimeout(1.0)
    print(f"Relay syslog UDP in ascolto su {bind_host}:{port}")
    while not _shutdown.is_set():
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            raw = data.decode("utf-8", errors="replace")
        except Exception:
            continue
        _forward(tenant, relay_hostname, raw, addr[0], build_gelf_message_fn, sock_holder, archiver)
    sock.close()


def _handle_tcp_client(conn: socket.socket, addr, tenant: str, relay_hostname: str,
                         build_gelf_message_fn, sock_holder: dict, archiver=None):
    buf = b""
    conn.settimeout(1.0)
    with conn:
        while not _shutdown.is_set():
            try:
                chunk = conn.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            # framing per newline - il piu' comune per syslog su TCP nella pratica
            # (l'octet-counting di RFC6587 e' meno diffuso fuori da implementazioni strette)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    try:
                        raw = line.decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    _forward(tenant, relay_hostname, raw, addr[0], build_gelf_message_fn, sock_holder, archiver)


def _tcp_listener(bind_host: str, port: int, tenant: str, relay_hostname: str,
                    build_gelf_message_fn, sock_holder: dict, archiver=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_host, port))
    sock.listen(20)
    sock.settimeout(1.0)
    print(f"Relay syslog TCP in ascolto su {bind_host}:{port}")
    while not _shutdown.is_set():
        try:
            conn, addr = sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        t = threading.Thread(
            target=_handle_tcp_client,
            args=(conn, addr, tenant, relay_hostname, build_gelf_message_fn, sock_holder, archiver),
            daemon=True,
        )
        t.start()
    sock.close()


def start_syslog_relay(tenant: str, relay_hostname: str, build_gelf_message_fn, sock_holder: dict,
                         udp_port: int | None = None, tcp_port: int | None = None,
                         bind_host: str = "0.0.0.0", archiver=None) -> list:
    """Avvia i listener richiesti in thread separati (demoni, si fermano
    da soli alla chiusura del processo) e ritorna la lista dei thread
    avviati, per completezza/debug - non serve fare nulla con quella
    lista per il funzionamento normale."""
    threads = []
    if udp_port:
        t = threading.Thread(target=_udp_listener,
                              args=(bind_host, udp_port, tenant, relay_hostname,
                                    build_gelf_message_fn, sock_holder, archiver),
                              daemon=True)
        t.start()
        threads.append(t)
    if tcp_port:
        t = threading.Thread(target=_tcp_listener,
                              args=(bind_host, tcp_port, tenant, relay_hostname,
                                    build_gelf_message_fn, sock_holder, archiver),
                              daemon=True)
        t.start()
        threads.append(t)
    return threads
