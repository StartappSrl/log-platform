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


def follow(path_str: str, tenant: str, hostname: str, connect_args: tuple, sock_holder: dict,
           log_class: str | None = None, archiver: LocalArchiver | None = None):
    path = Path(path_str)
    extra = {"source_file": str(path)}
    if log_class:
        extra["log_class"] = log_class
    for line in tail_file(path):
        if archiver:
            archiver.write_line(line)
        payload = build_gelf_message(tenant, hostname, line, extra=extra)
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
        for p in log_files + admin_log_files:
            archivers[p] = LocalArchiver(
                source_id=Path(p).name, archive_dir=local_archive_dir,
                tenant=tenant, hostname=hostname,
                tsa_url=tsa_url, tsa_user=tsa_user, tsa_password=tsa_password,
                tsa_client_cert=tsa_client_cert, tsa_client_key=tsa_client_key,
            )
        print(f"Archiviazione locale attiva: {local_archive_dir} "
              f"({len(archivers)} sorgenti, marca temporale {'attiva' if tsa_url else 'non configurata'})")

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
