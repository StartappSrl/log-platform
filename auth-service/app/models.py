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


class FailedLogin(db.Model):
    __tablename__ = "failed_logins"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), index=True)
    ip = db.Column(db.String(64))
    at = db.Column(db.DateTime, default=datetime.utcnow)
