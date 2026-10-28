"""تطبيق توزيع الطلاب على المراكز: create_app() بيبني تطبيق Flask كامل."""
import urllib.parse
from typing import Any, cast

from flask import Flask, jsonify, redirect, request, url_for
from flask_login import current_user

from . import config, auth, jobs

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_PUBLIC = {"auth.login", "auth.setup", "static", "pages.health"}


def create_app():
    config.ensure_dirs()
    templates_dir = getattr(config, "TEMPLATES_DIR", config.BASE_DIR / "frontend" / "templates")
    static_dir = getattr(config, "STATIC_DIR", config.BASE_DIR / "frontend" / "static")
    app = Flask(__name__, template_folder=str(templates_dir), static_folder=str(static_dir))
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024
    app.config["SECRET_KEY"] = config.secret_key()
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    cast(dict[str, Any], app.jinja_env.globals)["VERSION"] = config.VERSION
    app.context_processor(lambda: {"VERSION": config.VERSION})
    auth.login_manager.init_app(app)
    app.register_blueprint(auth.bp)
    from .web import api, pages
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    jobs.startup_cleanup()

    @app.before_request
    def _guard():
        # السيستم محلي: نرفض أي Host غير محلي (DNS rebinding) وأي POST جاي من صفحة موقع تاني (Origin مختلف)
        host = urllib.parse.urlsplit("//" + (request.host or "")).hostname
        if config.HOST in _LOCAL_HOSTS and host not in _LOCAL_HOSTS:
            return jsonify(error="Forbidden host"), 403
        origin = request.headers.get("Origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin is not None:
            if urllib.parse.urlsplit(origin).netloc != request.host:
                return jsonify(error="Forbidden origin"), 403
        if request.endpoint in _PUBLIC or request.endpoint is None:
            return None
        if not current_user.is_authenticated:
            if request.path.startswith("/api/"):
                return jsonify(error="لازم تسجّل الدخول الأول", login=True), 401
            if not auth.users_count():
                return redirect(url_for("auth.setup"))
            return redirect(url_for("auth.login", next=request.path))
        return None

    @app.errorhandler(413)
    def _too_big(_e):
        return jsonify(error=f"الملف أكبر من {config.MAX_UPLOAD_MB} ميجا."), 413

    return app
