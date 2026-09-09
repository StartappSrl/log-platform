"""
Protezione contro tentativi ripetuti di login/MFA (brute-force), basata
sulla tabella FailedLogin gia' presente nel modello dati.

Politica: dopo MAX_ATTEMPTS tentativi falliti (login o MFA) per lo stesso
username entro WINDOW_MINUTES minuti, i tentativi successivi vengono
rifiutati con 429 finche' la finestra non scorre. Conta sia gli username
inesistenti sia quelli esistenti con password/codice sbagliati, cosi' non
si puo' usare il login per scoprire quali username esistono ne' per
tentare a raffica su uno esistente.
"""
from datetime import datetime, timedelta

from .models import db, FailedLogin

MAX_ATTEMPTS = 5
WINDOW_MINUTES = 15


def is_locked_out(username: str) -> bool:
    if not username:
        return False
    cutoff = datetime.utcnow() - timedelta(minutes=WINDOW_MINUTES)
    count = FailedLogin.query.filter(
        FailedLogin.username == username,
        FailedLogin.at >= cutoff,
    ).count()
    return count >= MAX_ATTEMPTS


def record_failed_attempt(username: str, ip: str = ""):
    if not username:
        return
    db.session.add(FailedLogin(username=username, ip=ip or "", at=datetime.utcnow()))
    db.session.commit()


def clear_failed_attempts(username: str):
    """Da chiamare su login riuscito, cosi' un utente legittimo non resta
    penalizzato da vecchi tentativi falliti (es. password dimenticata due
    volte, poi ricordata)."""
    if not username:
        return
    FailedLogin.query.filter(FailedLogin.username == username).delete()
    db.session.commit()
