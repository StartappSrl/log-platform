import os
from datetime import datetime, timedelta

import bcrypt
from flask import Blueprint, current_app, jsonify, request, make_response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from .models import db, User, FailedLogin
from .mfa import generate_secret, provisioning_qr_base64, verify_code

bp = Blueprint("api", __name__, url_prefix="/_authgate")

MAX_FAILED_ATTEMPTS = 5
FAILED_WINDOW_MINUTES = 15


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["AUTH_SECRET_KEY"], salt="session")


def _set_session_cookie(resp, username: str):
    token = _serializer().dumps({"u": username})
    max_age = int(current_app.config["SESSION_LIFETIME_MINUTES"]) * 60
    resp.set_cookie(
        current_app.config["SESSION_COOKIE_NAME"],
        token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="Lax",
    )


def _too_many_failed_attempts(username: str, ip: str) -> bool:
    since = datetime.utcnow() - timedelta(minutes=FAILED_WINDOW_MINUTES)
    count = FailedLogin.query.filter(
        FailedLogin.username == username, FailedLogin.at >= since
    ).count()
    return count >= MAX_FAILED_ATTEMPTS


@bp.post("/login")
def login():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    ip = request.headers.get("X-Real-IP", request.remote_addr)

    if not username or not password:
        return jsonify(error="credenziali mancanti"), 400

    if _too_many_failed_attempts(username, ip):
        return jsonify(error="troppi tentativi falliti, riprova più tardi"), 429

    user = User.query.filter_by(username=username, is_active=True).first()
    if not user or not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        db.session.add(FailedLogin(username=username, ip=ip))
        db.session.commit()
        return jsonify(error="credenziali non valide"), 401

    if not user.mfa_confirmed:
        qr = provisioning_qr_base64(user.totp_secret, user.username)
        return jsonify(mfa_setup_required=True, qr_png_base64=qr), 200

    return jsonify(mfa_required=True), 200


@bp.post("/verify-mfa")
def verify_mfa():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    code = (data.get("code") or "").strip()

    user = User.query.filter_by(username=username, is_active=True).first()
    if not user:
        return jsonify(error="utente non valido"), 401

    if not verify_code(user.totp_secret, code):
        return jsonify(error="codice MFA non valido"), 401

    if not user.mfa_confirmed:
        user.mfa_confirmed = True
    user.last_login_at = datetime.utcnow()
    db.session.commit()

    resp = make_response(jsonify(ok=True))
    _set_session_cookie(resp, user.username)
    return resp


@bp.get("/auth_check")
def auth_check():
    """Usato da nginx (auth_request) per validare la sessione."""
    cookie_name = current_app.config["SESSION_COOKIE_NAME"]
    token = request.cookies.get(cookie_name)
    if not token:
        return "", 401

    max_age = int(current_app.config["SESSION_LIFETIME_MINUTES"]) * 60
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return "", 401

    user = User.query.filter_by(username=data.get("u"), is_active=True).first()
    if not user:
        return "", 401

    resp = make_response("", 200)
    resp.headers["X-Auth-User"] = user.username
    resp.headers["X-Auth-Tenant"] = user.tenant or "*"
    return resp


@bp.post("/logout")
def logout():
    resp = make_response(jsonify(ok=True))
    resp.delete_cookie(current_app.config["SESSION_COOKIE_NAME"])
    return resp
