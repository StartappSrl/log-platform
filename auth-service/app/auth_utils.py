"""Helper condiviso per leggere l'utente corrente dal cookie di sessione
firmato (lo stesso meccanismo usato in routes.py per /auth_check), così
sia le rotte di login sia quelle del pannello usano la stessa logica."""
from flask import current_app, request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from .models import User


def get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["AUTH_SECRET_KEY"], salt="session")


def get_current_user():
    """Ritorna l'oggetto User se il cookie di sessione è valido, altrimenti None."""
    cookie_name = current_app.config["SESSION_COOKIE_NAME"]
    token = request.cookies.get(cookie_name)
    if not token:
        return None

    max_age = int(current_app.config["SESSION_LIFETIME_MINUTES"]) * 60
    try:
        data = get_serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None

    return User.query.filter_by(username=data.get("u"), is_active=True).first()
