"""
Controllo di base dello stato della piattaforma stessa (Graylog,
MariaDB) - non sostituisce un vero monitoraggio infrastrutturale, ma da'
un primo segnale se qualcosa di essenziale non risponde.
"""
import time

from . import graylog_client as gl
from .models import db


def check_graylog() -> dict:
    start = time.monotonic()
    try:
        gl._request("GET", "/api/system")
        elapsed_ms = round((time.monotonic() - start) * 1000)
        return {"ok": True, "elapsed_ms": elapsed_ms, "error": None}
    except Exception as e:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        return {"ok": False, "elapsed_ms": elapsed_ms, "error": str(e)}


def check_mariadb() -> dict:
    from sqlalchemy import text
    start = time.monotonic()
    try:
        db.session.execute(text("SELECT 1"))
        elapsed_ms = round((time.monotonic() - start) * 1000)
        return {"ok": True, "elapsed_ms": elapsed_ms, "error": None}
    except Exception as e:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        return {"ok": False, "elapsed_ms": elapsed_ms, "error": str(e)}


def check_platform_health() -> dict:
    """Nota onesta: verifica solo che Graylog e MariaDB rispondano a una
    richiesta minima - non controlla OpenSearch direttamente (non
    abbiamo un client dedicato per quello), ne' spazio su disco, uso di
    CPU/RAM del nodo, o altri aspetti di un vero monitoraggio
    infrastrutturale. E' un segnale di base, non un sostituto di uno
    strumento di monitoraggio dedicato."""
    return {
        "graylog": check_graylog(),
        "mariadb": check_mariadb(),
        "checked_at": time.time(),
    }
