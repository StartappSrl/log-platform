"""
Agent Windows per l'invio di log al server Graylog via GELF TCP + TLS mutuo (mTLS).

A differenza della versione Linux, gira come SERVIZIO WINDOWS nativo (non serve
un tool esterno tipo NSSM) e sa leggere sia file di log testuali (es. log IIS)
sia i canali dell'Event Log di Windows (Application, System, Security, ...).

Requisiti: pip install pywin32   (poi eseguire, una volta:
    python -m pywin32_postinstall -install
se l'installazione non registra automaticamente i moduli di servizio)

Configurazione: stesso agent.ini della versione Linux, con in più una chiave
opzionale 'event_logs' nella sezione [agent], es.:
    event_logs = Application, System

Installazione come servizio (da un prompt Amministratore):
    python agent_windows.py install
    python agent_windows.py start

Altri comandi utili: 'stop', 'remove', 'restart', 'debug' (esecuzione in
primo piano per verificare che funzioni prima di installarlo come servizio).
"""
import configparser
import json
import os
import socket
import threading
import time
from pathlib import Path

import servicemanager
import win32event
import win32evtlog
import win32service
import win32serviceutil

from gelf_transport import build_gelf_message, connect

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = SCRIPT_DIR / "agent_state.json"


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state))
    except Exception:
        pass


def tail_file(path: Path, stop_flag: dict):
    with open(path, "r", errors="replace") as f:
        f.seek(0, 2)
        while not stop_flag["stop"]:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield line.rstrip("\n")


def follow_file(path_str: str, tenant: str, hostname: str, connect_args: tuple,
                 sock_holder: dict, stop_flag: dict, log_class: str | None = None):
    path = Path(path_str)
    extra_base = {"source_file": str(path)}
    if log_class:
        extra_base["log_class"] = log_class
    for line in tail_file(path, stop_flag):
        payload = build_gelf_message(tenant, hostname, line, extra=extra_base)
        while True:
            try:
                sock_holder["sock"].sendall(payload)
                break
            except (BrokenPipeError, OSError):
                if stop_flag["stop"]:
                    return
                time.sleep(2)
                sock_holder["sock"] = connect(*connect_args)


def follow_event_log(channel: str, tenant: str, hostname: str, connect_args: tuple,
                      sock_holder: dict, stop_flag: dict, state: dict, log_class: str | None = None):
    """Legge in polling i nuovi eventi di un canale Event Log (Application,
    System, Security, ...) e li inoltra come messaggi GELF. Tiene traccia
    dell'ultimo record letto in agent_state.json per non reinviare tutto
    ad ogni riavvio del servizio."""
    level_map = {
        win32evtlog.EVENTLOG_ERROR_TYPE: 3,
        win32evtlog.EVENTLOG_WARNING_TYPE: 4,
        win32evtlog.EVENTLOG_INFORMATION_TYPE: 6,
        win32evtlog.EVENTLOG_AUDIT_SUCCESS: 6,
        win32evtlog.EVENTLOG_AUDIT_FAILURE: 4,
    }

    last_seen = state.get(f"eventlog:{channel}", 0)

    while not stop_flag["stop"]:
        try:
            hand = win32evtlog.OpenEventLog(None, channel)
            flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
            events = win32evtlog.ReadEventLog(hand, flags, 0)
            new_events = []
            for ev in events:
                if ev.RecordNumber <= last_seen:
                    break
                new_events.append(ev)
            win32evtlog.CloseEventLog(hand)

            for ev in reversed(new_events):  # invia in ordine cronologico
                try:
                    message = " ".join(ev.StringInserts) if ev.StringInserts else str(ev.EventID)
                except Exception:
                    message = f"EventID {ev.EventID}"

                extra = {
                    "event_channel": channel,
                    "event_id": ev.EventID & 0xFFFF,
                    "event_source": ev.SourceName,
                    "event_record_number": ev.RecordNumber,
                }
                if log_class:
                    extra["log_class"] = log_class

                payload = build_gelf_message(
                    tenant, hostname, message,
                    level=level_map.get(ev.EventType, 6),
                    extra=extra,
                )
                while True:
                    try:
                        sock_holder["sock"].sendall(payload)
                        break
                    except (BrokenPipeError, OSError):
                        if stop_flag["stop"]:
                            return
                        time.sleep(2)
                        sock_holder["sock"] = connect(*connect_args)

                last_seen = max(last_seen, ev.RecordNumber)

            if new_events:
                state[f"eventlog:{channel}"] = last_seen
                _save_state(state)

        except Exception:
            pass  # canale non disponibile in questo giro, riprova al prossimo poll

        for _ in range(10):  # polling ogni ~5s, ma reattivo allo stop
            if stop_flag["stop"]:
                return
            time.sleep(0.5)


def load_config(config_path: Path):
    cfg = configparser.ConfigParser()
    cfg.read(config_path)
    s = cfg["agent"]
    return {
        "tenant": s["tenant"],
        "hostname": s.get("hostname", socket.gethostname()),
        "graylog_host": s["graylog_host"],
        "graylog_port": s.getint("graylog_port", 12201),
        "ca_cert": str(SCRIPT_DIR / s["ca_cert"]),
        "client_cert": str(SCRIPT_DIR / s["client_cert"]),
        "client_key": str(SCRIPT_DIR / s["client_key"]),
        "log_files": [p.strip() for p in s.get("log_files", "").split(",") if p.strip()],
        "event_logs": [c.strip() for c in s.get("event_logs", "").split(",") if c.strip()],
        "admin_log_files": [p.strip() for p in s.get("admin_log_files", "").split(",") if p.strip()],
        # Canali Event Log da taggare come log_class=admin-access (tipicamente "Security",
        # dove Windows registra i logon: Event ID 4624/4625/4672). Vedi
        # scripts/provision-admin-log-stream.sh e seal-admin-logs.sh.
        "admin_event_logs": [c.strip() for c in s.get("admin_event_logs", "").split(",") if c.strip()],
    }


def run_agent(stop_flag: dict):
    config = load_config(SCRIPT_DIR / "agent.ini")
    connect_args = (config["graylog_host"], config["graylog_port"],
                     config["ca_cert"], config["client_cert"], config["client_key"])
    sock_holder = {"sock": connect(*connect_args)}
    state = _load_state()

    threads = []
    for p in config["log_files"]:
        threads.append(threading.Thread(
            target=follow_file,
            args=(p, config["tenant"], config["hostname"], connect_args, sock_holder, stop_flag),
            daemon=True))
    for p in config["admin_log_files"]:
        threads.append(threading.Thread(
            target=follow_file,
            args=(p, config["tenant"], config["hostname"], connect_args, sock_holder, stop_flag, "admin-access"),
            daemon=True))
    for channel in config["event_logs"]:
        threads.append(threading.Thread(
            target=follow_event_log,
            args=(channel, config["tenant"], config["hostname"], connect_args, sock_holder, stop_flag, state, None),
            daemon=True))
    for channel in config["admin_event_logs"]:
        threads.append(threading.Thread(
            target=follow_event_log,
            args=(channel, config["tenant"], config["hostname"], connect_args, sock_holder, stop_flag, state, "admin-access"),
            daemon=True))

    if not threads:
        raise SystemExit("Configura almeno 'log_files', 'admin_log_files', 'event_logs' o 'admin_event_logs' in agent.ini")

    for t in threads:
        t.start()
    return threads


class LogPlatformAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "LogPlatformAgent"
    _svc_display_name_ = "Log Platform Agent"
    _svc_description_ = "Invia log (file e/o Event Log) al server Log Platform via GELF/mTLS"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.stop_flag = {"stop": False}

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stop_flag["stop"] = True
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""))
        run_agent(self.stop_flag)
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(LogPlatformAgentService)
