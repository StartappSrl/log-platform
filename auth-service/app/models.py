from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    totp_secret = db.Column(db.String(64), nullable=False)
    mfa_confirmed = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    tenant = db.Column(db.String(120), nullable=True)  # None/"*" = accesso globale (admin)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)


class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(200), nullable=True)
    graylog_stream_id = db.Column(db.String(64), nullable=True)
    graylog_index_set_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Anagrafica cliente (facoltativa, per intestare bene report/fatture)
    ragione_sociale = db.Column(db.String(255), nullable=True)
    partita_iva = db.Column(db.String(50), nullable=True)
    indirizzo = db.Column(db.String(500), nullable=True)
    referente = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    note = db.Column(db.Text, nullable=True)


class FailedLogin(db.Model):
    __tablename__ = "failed_logins"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), index=True)
    ip = db.Column(db.String(64))
    at = db.Column(db.DateTime, default=datetime.utcnow)

class SmtpSettings(db.Model):
    __tablename__ = "smtp_settings"

    id = db.Column(db.Integer, primary_key=True)
    smtp_host = db.Column(db.String(255), nullable=True)
    smtp_port = db.Column(db.Integer, nullable=True, default=587)
    smtp_user = db.Column(db.String(255), nullable=True)
    smtp_password = db.Column(db.String(255), nullable=True)
    smtp_from = db.Column(db.String(255), nullable=True)
    smtp_use_tls = db.Column(db.Boolean, nullable=False, default=True)
    report_recipients = db.Column(db.String(1000), nullable=True)

