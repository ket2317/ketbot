import logging

from flask import Flask
from flask_cors import CORS

from clients import seed_initial_clients
from config import Config
from database import init_db, session_scope
from routes import admin_bp, appointments_bp


def create_app() -> Flask:
    logging.basicConfig(
        level=Config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    app = Flask(__name__)
    app.config["SECRET_KEY"] = Config.SECRET_KEY or "admin-disabled"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    CORS(
        app,
        resources={r"/*": {"origins": Config.CORS_ORIGINS}},
        methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        expose_headers=["Content-Type"],
        max_age=86400,
    )
    app.register_blueprint(appointments_bp)
    app.register_blueprint(admin_bp)

    init_db()
    with session_scope() as session:
        seed_initial_clients(session)

    registered_routes = sorted(
        f"{','.join(sorted(rule.methods - {'HEAD', 'OPTIONS'})) or 'OPTIONS'} {rule.rule}"
        for rule in app.url_map.iter_rules()
        if rule.endpoint != "static"
    )
    logger.info("registered_routes routes=%s", registered_routes)

    @app.after_request
    def add_vapi_friendly_headers(response):
        response.headers.setdefault("Access-Control-Allow-Origin", "*")
        response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type, Authorization")
        response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        response.headers.setdefault("Content-Type", "application/json")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=Config.FLASK_HOST, port=Config.PORT)
