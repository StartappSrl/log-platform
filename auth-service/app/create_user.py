"""Crea (o resetta) un utente. Uso:
    docker compose exec auth-service python -m app.create_user
"""
import getpass
import sys

import bcrypt

from . import create_app
from .models import db, User
from .mfa import generate_secret


def main():
    app = create_app()
    with app.app_context():
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        password2 = getpass.getpass("Ripeti password: ")
        if password != password2:
            print("Le password non corrispondono.")
            sys.exit(1)

        tenant = input("Tenant (vuoto = accesso globale/admin): ").strip() or None

        existing = User.query.filter_by(username=username).first()
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        if existing:
            existing.password_hash = pw_hash
            existing.tenant = tenant
            existing.mfa_confirmed = False
            existing.totp_secret = generate_secret()
            db.session.commit()
            print(f"Utente '{username}' aggiornato. Al primo login dovrà rifare il setup MFA.")
        else:
            user = User(
                username=username,
                password_hash=pw_hash,
                totp_secret=generate_secret(),
                tenant=tenant,
            )
            db.session.add(user)
            db.session.commit()
            print(f"Utente '{username}' creato.")


if __name__ == "__main__":
    main()
