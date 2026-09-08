import os

from flask import Flask

from .models import db


def create_app() -> Flask:
    app = Flask(__name__, static_folder="../static", static_url_path="")

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["AUTH_SECRET_KEY"] = os.environ["AUTH_SECRET_KEY"]
    app.config["SESSION_COOKIE_NAME"] = os.environ.get("SESSION_COOKIE_NAME", "logplatform_session")
    app.config["SESSION_LIFETIME_MINUTES"] = os.environ.get("SESSION_LIFETIME_MINUTES", "480")

    db.init_app(app)

    from .routes import bp
    app.register_blueprint(bp)

    from .dashboard import dash
    app.register_blueprint(dash)

    from .setup_routes import setup_bp
    app.register_blueprint(setup_bp)

    with app.app_context():
        db.create_all()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
