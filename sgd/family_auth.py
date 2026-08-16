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

from flask import abort, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from sgd import app, db
from sgd.branding import admin_whatsapp_link

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 6


@app.route("/login", methods=["GET", "POST"])
def family_login():
    error = None
    account_unavailable = False
    email = ""
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
            error = "Sua conta está inativa ou expirada."
            account_unavailable = True
        else:
            error = "E-mail ou senha incorretos."

    return render_template(
        "auth/login.html",
        error=error,
        account_unavailable=account_unavailable,
        support_url=admin_whatsapp_link("Preciso de ajuda com meu acesso ao Stream Helium."),
        email=email,
    )


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
