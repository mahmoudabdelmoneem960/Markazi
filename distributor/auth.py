"""تسجيل الدخول: مستخدم أدمن واحد (أو أكتر) بكلمة سر مشفّرة. أول تشغيل بيفتح صفحة إنشاء الأدمن."""
from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from distributor import db

bp = Blueprint("auth", __name__)
login_manager = LoginManager()
login_manager.login_view = "auth.login"


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.username = row["username"]


@login_manager.user_loader
def load_user(uid):
    con = db.connect()
    try:
        r = con.execute("SELECT id, username FROM users WHERE id=?", (uid,)).fetchone()
    finally:
        con.close()
    return User(r) if r else None


def users_count():
    con = db.connect()
    try:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        con.close()


def _valid_password(pw):
    return isinstance(pw, str) and len(pw) >= 8


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if users_count():
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        u = (request.form.get("username") or "admin").strip()
        pw = request.form.get("password") or ""
        if not _valid_password(pw) or pw != (request.form.get("password2") or ""):
            return render_template("setup.html", error="كلمة السر لازم 8 حروف على الأقل والاتنين متطابقين."), 400
        con = db.connect()
        try:
            con.execute("INSERT INTO users(username, pw_hash, created_at) VALUES (?,?,?)", (u, generate_password_hash(pw), db.now()))
            con.commit()
            r = con.execute("SELECT id, username FROM users WHERE username=?", (u,)).fetchone()
        finally:
            con.close()
        login_user(User(r), remember=True)
        return redirect(url_for("pages.dashboard"))
    return render_template("setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not users_count():
        return redirect(url_for("auth.setup"))
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        pw = request.form.get("password") or ""
        con = db.connect()
        try:
            r = con.execute("SELECT id, username, pw_hash FROM users WHERE username=?", (u,)).fetchone()
        finally:
            con.close()
        if r and check_password_hash(r["pw_hash"], pw):
            login_user(User(r), remember=True)
            return redirect(request.args.get("next") or url_for("pages.dashboard"))
        return render_template("login.html", error="اسم المستخدم أو كلمة السر غلط."), 401
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/api/password", methods=["POST"])
def change_password():
    d = request.get_json() or {}
    old, new = d.get("old") or "", d.get("new") or ""
    if not _valid_password(new):
        return jsonify(error="كلمة السر الجديدة لازم 8 حروف على الأقل."), 400
    con = db.connect()
    try:
        r = con.execute("SELECT pw_hash FROM users WHERE id=?", (current_user.id,)).fetchone()
        if not r or not check_password_hash(r["pw_hash"], old):
            return jsonify(error="كلمة السر الحالية غلط."), 400
        con.execute("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(new), current_user.id))
        con.commit()
    finally:
        con.close()
    return jsonify(ok=True)
