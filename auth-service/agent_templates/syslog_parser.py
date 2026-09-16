"""
Parser per messaggi syslog (RFC3164 "vecchio stile" e RFC5424 - la
maggioranza dei dispositivi di rete usa ancora RFC3164). Estrae
l'hostname REALE del dispositivo che ha generato il log (non quello del
relay che lo inoltra), e la gravita' vera (dal campo PRI, non indovinata
dal testo) - piu' affidabile della semplice ricerca di parole chiave
usata per i file di log generici senza PRI.
"""
import re

_PRI_PATTERN = re.compile(r"^<(\d{1,3})>")
_RFC5424_PATTERN = re.compile(
    r"^<(\d{1,3})>(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.*)$"
)
_RFC3164_PATTERN = re.compile(
    r"^<(\d{1,3})>(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s+(.*)$"
)


def parse_syslog_message(raw: str) -> dict:
    """Ritorna {'hostname': str|None, 'level': int (0-7), 'message': str}.
    Se il messaggio non ha un PRI riconoscibile (dispositivo non
    conforme, o qualcuno manda testo semplice), ritorna il messaggio
    cosi' com'e' con hostname=None e livello 6 (informativo) di
    default - il chiamante puo' allora usare l'hostname del mittente
    IP/relay come fallback."""
    raw = raw.strip()
    pri_match = _PRI_PATTERN.match(raw)
    if not pri_match:
        return {"hostname": None, "level": 6, "message": raw}

    pri = int(pri_match.group(1))
    severity = pri % 8  # scala 0-7, stessa identica scala usata da GELF 'level'

    m5424 = _RFC5424_PATTERN.match(raw)
    if m5424:
        _, version, timestamp, hostname, appname, procid, msgid, rest = m5424.groups()
        message = rest
        if rest.startswith("["):
            end = rest.find("] ")
            if end != -1:
                message = rest[end + 2:]
        elif rest.startswith("- "):
            message = rest[2:]
        return {"hostname": hostname if hostname != "-" else None,
                "level": severity, "message": message}

    m3164 = _RFC3164_PATTERN.match(raw)
    if m3164:
        _, timestamp, hostname, message = m3164.groups()
        return {"hostname": hostname, "level": severity, "message": message}

    # PRI riconosciuto ma il resto non segue nessuno dei due formati standard -
    # almeno la gravita' e' affidabile, il resto lo trattiamo come testo libero
    rest = raw[pri_match.end():]
    return {"hostname": None, "level": severity, "message": rest}
