"""صفحات HTML. البيانات بتتحمّل من الـAPI بالجافاسكريبت."""
from flask import Blueprint, abort, jsonify, render_template, request

from distributor import config, db
from distributor.core import engine, locdb
from distributor.services import runs

bp = Blueprint("pages", __name__)


@bp.route("/health")
def health():
    return jsonify(ok=True, version=config.VERSION)


@bp.route("/")
def dashboard():
    return render_template("dashboard.html")


@bp.route("/runs/new")
def run_new():
    con = db.connect()
    try:
        s = db.get_settings(con)
    finally:
        con.close()
    return render_template("run_new.html", settings=s, has_gazetteer=bool(locdb.find_gazetteer()))


@bp.route("/runs/<run_id>")
def run_page(run_id):
    run = runs.get_run(run_id)
    if not run:
        abort(404)
    return render_template("run.html", run=run, use_map=True)


@bp.route("/centers")
def centers_page():
    return render_template("centers.html", use_map=True)


@bp.route("/review")
def review_page():
    return render_template("review.html", run_id=request.args.get("run", ""), use_map=True)


@bp.route("/compare")
def compare_page():
    return render_template("compare.html", a=request.args.get("a", ""), b=request.args.get("b", ""))


@bp.route("/settings")
def settings_page():
    con = db.connect()
    try:
        s = db.get_settings(con)
    finally:
        con.close()
    return render_template("settings.html", settings=s, paths={"data": str(config.DATA_DIR), "db": str(config.DB_PATH),
                                                                "gazetteer": locdb.find_gazetteer() or "", "osrm_cache": str(config.OSRM_CACHE),
                                                                "google_key": str(config.GOOGLE_KEY_FILE)},
                           defaults={"max_km": engine.MAX_KM, "detour": engine.DETOUR})
