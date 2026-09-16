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

ARCHIVIAZIONE LOCALE: se 'local_archive_dir' è impostato in agent.ini,
ogni file monitorato viene anche scritto in un archivio locale compresso e
sigillato (hash chain + marca temporale opzionale). Vedi local_archive.py.

INVENTARIO (nuovo): se 'inventory_interval_hours' è impostato (default 24,
0 per disattivare), l'agent invia periodicamente un messaggio speciale
(log_class=inventory) con hardware, software installato e risorse della
macchina. Vedi inventory.py.

Uso tipico: seguire (tail -f) uno o più file di log e inoltrarli.
    python3 agent.py --config agent.ini

Per installarlo come servizio persistente, vedi logplatform-agent.service.example.
Per Windows, usa agent_windows.py invece di questo script.
"""
import argparse
import configparser
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from gelf_transport import build_gelf_message, connect
from local_archive import LocalArchiver
from inventory import build_inventory_gelf_message

_shutdown = threading.Event()


def tail_file(path: Path):
    with open(path, "r", errors="replace") as f:
        f.seek(0, 2)  # vai alla fine, invia solo le righe nuove
        while not _shutdown.is_set():
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield line.rstrip("\n")


import re

_ERROR_PATTERN = re.compile(r"\b(error|critical|crit|fatal|failed|failure|exception|panic)\b", re.IGNORECASE)
_WARNING_PATTERN = re.compile(r"\b(warn|warning|deprecat)\w*\b", re.IGNORECASE)


def _guess_level_from_text(line: str) -> int:
    """Indovina un livello di gravita' GELF (standard syslog: 3=errore,
    4=avviso, 6=informativo) analizzando parole chiave comuni nel testo
    della riga di log. Non e' un parser dei formati di log specifici
    (es. non capisce codici di errore numerici propri di
    un'applicazione) - e' un'euristica su parole comuni in inglese,
    pensata per dare un minimo di segnale sui file di log generici
    (es. syslog) che altrimenti arriverebbero sempre come 'informativo'.
    Se la riga contiene sia 'error' sia 'warning', vince errore (piu'
    grave, meglio segnalare per eccesso che perderlo)."""
    if _ERROR_PATTERN.search(line):
        return 3
    if _WARNING_PATTERN.search(line):
        return 4
    return 6


def follow(path_str: str, tenant: str, hostname: str, connect_args: tuple, sock_holder: dict,
           log_class: str | None = None, archiver: LocalArchiver | None = None):
    path = Path(path_str)
    extra = {"source_file": str(path)}
    if log_class:
        extra["log_class"] = log_class
    for line in tail_file(path):
        if archiver:
            archiver.write_line(line)
        level = _guess_level_from_text(line)
        payload = build_gelf_message(tenant, hostname, line, level=level, extra=extra)
        while True:
            try:
                sock_holder["sock"].sendall(payload)
                break
            except (BrokenPipeError, OSError):
                if _shutdown.is_set():
                    return
                time.sleep(2)
                sock_holder["sock"] = connect(*connect_args)


def inventory_loop(tenant: str, hostname: str, connect_args: tuple, sock_holder: dict,
                    interval_hours: float):
    """Invia un inventario hardware/software/risorse subito all'avvio, poi
    ogni 'interval_hours' ore. Gira finché l'agent non viene fermato."""
    while not _shutdown.is_set():
        try:
            payload = build_inventory_gelf_message(tenant, hostname, build_gelf_message)
            while True:
                try:
                    sock_holder["sock"].sendall(payload)
                    break
                except (BrokenPipeError, OSError):
                    if _shutdown.is_set():
                        return
                    time.sleep(2)
                    sock_holder["sock"] = connect(*connect_args)
            print(f"Inventario inviato ({len(payload)} byte)")
        except Exception as e:
            print(f"Errore raccolta/invio inventario: {e}", file=sys.stderr)

        # Attesa interrompibile (controlla lo shutdown ogni secondo, invece
        # di un unico time.sleep lungo che ritarderebbe l'arresto pulito)
        seconds_total = max(interval_hours, 0.01) * 3600
        for _ in range(int(seconds_total)):
            if _shutdown.is_set():
                return
            time.sleep(1)


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
    inventory_interval_hours = s.getfloat("inventory_interval_hours", 24)

    if not log_files and not admin_log_files:
        raise SystemExit("Configura almeno un file in 'log_files' o 'admin_log_files' nell'agent.ini")

    # --- Archiviazione locale (opzionale) ---
    local_archive_dir = s.get("local_archive_dir", "").strip()
    archivers = {}
    if local_archive_dir:
        tsa_url = s.get("local_archive_tsa_url", "").strip() or None
        tsa_user = s.get("local_archive_tsa_user", "").strip() or None
        tsa_password = s.get("local_archive_tsa_password", "").strip() or None
        tsa_client_cert = s.get("local_archive_tsa_client_cert", "").strip() or None
        tsa_client_key = s.get("local_archive_tsa_client_key", "").strip() or None
        upload_url = s.get("local_archive_upload_url", "").strip() or None
        upload_token = s.get("local_archive_upload_token", "").strip() or None
        for p in log_files + admin_log_files:
            archivers[p] = LocalArchiver(
                source_id=Path(p).name, archive_dir=local_archive_dir,
                tenant=tenant, hostname=hostname,
                tsa_url=tsa_url, tsa_user=tsa_user, tsa_password=tsa_password,
                tsa_client_cert=tsa_client_cert, tsa_client_key=tsa_client_key,
                upload_url=upload_url, upload_token=upload_token,
            )
        print(f"Archiviazione locale attiva: {local_archive_dir} "
              f"({len(archivers)} sorgenti, marca temporale {'attiva' if tsa_url else 'non configurata'}, "
              f"upload al portale {'attivo' if upload_url else 'non configurato'})")

    connect_args = (graylog_host, graylog_port, ca_cert, client_cert, client_key)
    sock_holder = {"sock": connect(*connect_args)}

    def handle_signal(signum, frame):
        print("Arresto in corso, sigillo gli archivi locali del giorno in corso...")
        _shutdown.set()
        for a in archivers.values():
            a.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # --- Relay syslog (opzionale) ---
    syslog_udp_port = s.getint("local_syslog_udp_port", fallback=0) or None
    syslog_tcp_port = s.getint("local_syslog_tcp_port", fallback=0) or None
    if syslog_udp_port or syslog_tcp_port:
        from syslog_relay import start_syslog_relay
        start_syslog_relay(
            tenant=tenant, relay_hostname=hostname,
            build_gelf_message_fn=build_gelf_message, sock_holder=sock_holder,
            udp_port=syslog_udp_port, tcp_port=syslog_tcp_port,
        )
        print(f"Relay syslog attivo (UDP: {syslog_udp_port or 'no'}, TCP: {syslog_tcp_port or 'no'})")

    threads = []
    for p in log_files:
        threads.append(threading.Thread(
            target=follow, args=(p, tenant, hostname, connect_args, sock_holder, None, archivers.get(p)),
            daemon=True))
    for p in admin_log_files:
        threads.append(threading.Thread(
            target=follow, args=(p, tenant, hostname, connect_args, sock_holder, "admin-access", archivers.get(p)),
            daemon=True))

    if inventory_interval_hours > 0:
        threads.append(threading.Thread(
            target=inventory_loop, args=(tenant, hostname, connect_args, sock_holder, inventory_interval_hours),
            daemon=True))
        print(f"Invio inventario attivo, ogni {inventory_interval_hours} ore")

    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
