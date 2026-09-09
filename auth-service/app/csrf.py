"""
Protezione CSRF per le azioni che modificano stato nel pannello (creazione
tenant, allarmi, notifiche, ecc.). Il cookie di sessione e' gia' SameSite=
Lax (mitiga parzialmente), ma non e' un vero token CSRF: un sito esterno
non potrebbe leggere il cookie, ma con SameSite=Lax alcune richieste
"top-level" (es. un form che naviga verso il nostro dominio) lo
includerebbero comunque. Un token esplicito, generato per sessione e
verificato ad ogni richiesta che modifica stato, chiude anche questo
spiraglio.

Uso:
    1. GET /_authgate/dashboard/csrf-token  -> {"csrf_token": "..."}
       (il frontend lo richiede una volta dopo il login, lo tiene in
       memoria, e lo manda come header X-CSRF-Token su ogni POST/DELETE)
    2. Decorare le rotte che modificano stato con @csrf_protect
"""
import hmac
from functools import wraps

from flask import current_app, g, jsonify, request

from .auth_utils import get_serializer


def generate_csrf_token(username: str) -> str:
    return get_serializer().dumps({"csrf_for": username})


def _valid_csrf_token(token: str, username: str) -> bool:
    if not token:
        return False
    try:
        data = get_serializer().loads(token, max_age=int(current_app.config["SESSION_LIFETIME_MINUTES"]) * 60)
    except Exception:
        return False
    return hmac.compare_digest(data.get("csrf_for", ""), username)


def csrf_protect(fn):
    """Applicare DOPO @login_required (cosi' g.current_user esiste gia')."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-CSRF-Token", "")
        if not _valid_csrf_token(token, g.current_user.username):
            return jsonify(error="token CSRF mancante o non valido"), 403
        return fn(*args, **kwargs)
    return wrapper
