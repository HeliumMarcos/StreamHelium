"""Family (viewer) account login.

Separate session key from the admin's ("family_user_id" vs "is_admin") so
the same browser could in principle hold both without conflict, though in
practice each person is one or the other.

The invite-link URL (/u/<user_id>/, /connect/<invite_token>) keeps working
independently of login - this is an ADDITIONAL way in for people who'd
rather remember an email+password than bookmark a long URL.
"""

import logging
from datetime import datetime, timezone

from flask import abort, redirect, request, session, Response
from werkzeug.security import check_password_hash, generate_password_hash

from sgd import app, db
from sgd.branding import admin_whatsapp_html

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 6


def _page(body, title="Stream Helium"):
    html = f"""
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<style>
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
          background:#0a0710; color:#ece8f4; font:15px/1.6 system-ui,sans-serif; padding:24px; }}
  .box {{ max-width:22rem; width:100%; text-align:center; }}
  input {{ width:100%; padding:.6rem; border-radius:.4rem; border:1px solid #332844;
           background:#120c1c; color:#ece8f4; margin-bottom:.6rem; box-sizing:border-box; }}
  button {{ width:100%; padding:.6rem; border-radius:.4rem; border:none; cursor:pointer;
            background:#8b5cf6; color:#fff; font-weight:600; }}
  a {{ color:#22d3ee; }}
  .error {{ color:#ef4444; }}
</style>
<div class="box">{body}</div>
"""
    return Response(html, mimetype="text/html; charset=utf-8")


@app.route("/login", methods=["GET", "POST"])
def family_login():
    error = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user_row = db.get_user_by_email(email) if email else None

        valid = (
            user_row
            and user_row.get("password_hash")
            and check_password_hash(user_row["password_hash"], password)
        )
        if valid and db.is_effectively_active(user_row):
            session["family_user_id"] = str(user_row["id"])
            return redirect("/home")
        elif valid:
            error = (
                '<p class="error">Sua conta está inativa ou expirada.</p>'
                f'<p style="font-size:.85rem">{admin_whatsapp_html()}</p>'
            )
        else:
            error = '<p class="error">Email ou senha incorretos.</p>'

    return _page(f"""
      <h1>🎬 Stream Helium</h1>
      {error}
      <form method="post">
        <input type="email" name="email" placeholder="Seu email" required autofocus>
        <input type="password" name="password" placeholder="Sua senha" required>
        <button type="submit">Entrar</button>
      </form>
      <p style="margin-top:1rem;color:#a79fbb;font-size:.85rem">
        Ainda não definiu uma senha? Use o link de convite que você recebeu.
      </p>
      <p style="margin-top:1.5rem;color:#5f5670;font-size:.78rem">
        <a href="/admin/login" style="color:#5f5670">Sou administrador</a>
      </p>
    """, title="Entrar - Stream Helium")


@app.route("/logout", methods=["POST"])
def family_logout():
    session.pop("family_user_id", None)
    return redirect("/login")


@app.route("/home")
def family_home():
    user_id = session.get("family_user_id")
    if not user_id:
        return redirect("/login")

    user_row = db.get_user(user_id)
    if not user_row or not db.is_effectively_active(user_row):
        session.pop("family_user_id", None)
        return redirect("/login")

    return redirect(f"/u/{user_id}/")


def build_account_status(user_row):
    """Shared with /u/<id>/ (routes.py) so the same status card shows up
    whether someone arrives via login or via their direct URL."""
    if not user_row.get("active"):
        return {"active": False}
    expires_at = user_row.get("expires_at")
    if not expires_at:
        return {"active": True, "days_left": None}
    now = datetime.now(timezone.utc)
    if expires_at <= now:
        return {"active": True, "expired": True}
    days_left = (expires_at - now).days
    return {"active": True, "days_left": max(days_left, 0)}


# --- password setup, reached from the invite page (sgd/oauth.py) --------

@app.route("/connect/<invite_token>/set-password", methods=["POST"])
def connect_set_password(invite_token):
    user_row = db.get_user_by_invite_token(invite_token)
    if not user_row or not db.is_effectively_active(user_row):
        abort(404)

    password = request.form.get("password") or ""
    confirm = request.form.get("confirm") or ""
    if password != confirm or len(password) < MIN_PASSWORD_LENGTH:
        return redirect(f"/connect/{invite_token}?password_error=1")

    db.set_password(user_row["id"], generate_password_hash(password))
    return redirect(f"/connect/{invite_token}")
