"""THIL account portal and Caddy forward-auth service."""
import os
import secrets
import sqlite3
import time
from functools import wraps

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash


HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("THIL_USERS_DB", os.path.join(HERE, "thil_users.db"))
app = Flask(__name__)
app.secret_key = os.environ.get("THIL_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_NAME="thil_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("THIL_COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
)
_attempts = {}


def password_hash(password):
    return generate_password_hash(password, method="pbkdf2:sha256:600000")


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
            can_mhra INTEGER NOT NULL DEFAULT 0, can_pid INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    with connect() as con:
        return con.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()


def csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def require_csrf():
    if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "-")):
        abort(400)


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if not user["is_admin"]:
            abort(403)
        return fn(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_globals():
    return {"me": current_user(), "csrf_token": csrf()}


@app.get("/THIL/login")
def login():
    if current_user():
        return redirect("/THIL")
    return render_template("thil_login.html")


@app.post("/THIL/login")
def login_post():
    require_csrf()
    key = request.remote_addr or "unknown"
    recent = [t for t in _attempts.get(key, []) if time.time() - t < 900]
    _attempts[key] = recent
    if len(recent) >= 10:
        flash("Too many attempts. Please wait 15 minutes.", "error")
        return redirect("/THIL/login"), 429
    username = request.form.get("username", "").strip()
    with connect() as con:
        user = con.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], request.form.get("password", "")):
        recent.append(time.time())
        flash("Incorrect username or password.", "error")
        return redirect("/THIL/login")
    _attempts.pop(key, None)
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    csrf()
    nxt = request.args.get("next", "/THIL")
    if not nxt.startswith("/THIL") or nxt.startswith("//"):
        nxt = "/THIL"
    return redirect(nxt)


@app.post("/THIL/logout")
def logout():
    require_csrf()
    session.clear()
    return redirect("/THIL/login")


@app.get("/THIL")
@app.get("/THIL/")
def dashboard():
    user = current_user()
    if not user:
        return redirect("/THIL/login")
    return render_template("thil_dashboard.html")


@app.get("/THIL/brand-logo")
def brand_logo():
    if not current_user():
        abort(404)
    return send_from_directory(os.path.join(HERE, "static"), "target_healthcare_logo.png")


@app.get("/auth/check")
def auth_check():
    user = current_user()
    original = request.headers.get("X-Forwarded-Uri", "/THIL")
    if not user:
        return redirect(url_for("login", next=original))
    area = request.args.get("app", "")
    allowed = user["is_admin"] or (area == "mhra" and user["can_mhra"]) or (area == "pid" and user["can_pid"])
    return ("", 204) if allowed else ("Access denied", 403)


@app.get("/THIL/PID")
def pid_placeholder():
    user = current_user()
    if not user:
        return redirect(url_for("login", next=request.path))
    if not (user["is_admin"] or user["can_pid"]):
        abort(403)
    return render_template("thil_pid.html")


@app.get("/THIL/admin")
@admin_required
def admin():
    with connect() as con:
        users = con.execute("SELECT * FROM users ORDER BY username").fetchall()
    return render_template("thil_admin.html", users=users)


@app.post("/THIL/admin/users")
@admin_required
def create_user():
    require_csrf()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or len(username) > 64 or len(password) < 12:
        flash("Use a username and a password of at least 12 characters.", "error")
        return redirect("/THIL/admin")
    try:
        with connect() as con:
            con.execute("INSERT INTO users(username,password_hash,is_admin,can_mhra,can_pid) VALUES(?,?,?,?,?)",
                        (username, password_hash(password), "is_admin" in request.form,
                         "can_mhra" in request.form, "can_pid" in request.form))
        flash("User created.", "ok")
    except sqlite3.IntegrityError:
        flash("That username already exists.", "error")
    return redirect("/THIL/admin")


@app.post("/THIL/admin/users/<int:uid>/update")
@admin_required
def update_user(uid):
    require_csrf()
    me = current_user()
    if uid == me["id"] and ("active" not in request.form or "is_admin" not in request.form):
        flash("You cannot disable or remove administrator rights from your own account.", "error")
        return redirect("/THIL/admin")
    password = request.form.get("password", "")
    if password and len(password) < 12:
        flash("New passwords must be at least 12 characters.", "error")
        return redirect("/THIL/admin")
    with connect() as con:
        con.execute("UPDATE users SET is_admin=?,can_mhra=?,can_pid=?,active=? WHERE id=?",
                    ("is_admin" in request.form, "can_mhra" in request.form,
                     "can_pid" in request.form, "active" in request.form, uid))
        if password:
            con.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash(password), uid))
    flash("User updated.", "ok")
    return redirect("/THIL/admin")


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8095")))
