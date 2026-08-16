"""Google OAuth flow - admin-only now.

The admin connects each Drive account in the pool. Family (viewer) accounts
no longer go through Google OAuth at all - they're just assigned to one of
the pool accounts by the admin/system.

Requires a Google Cloud OAuth client of type **Web application** with this
exact redirect URI registered:

    https://<your-vercel-domain>/oauth/callback

Env vars required: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET.
"""

import hmac
import logging
import os
import secrets
from urllib.parse import urlencode

import requests
from flask import abort, redirect, render_template, request, session

from sgd import app, db
from sgd.tenancy import _drive_cache

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"

OAUTH_STATE_SESSION_KEY = "drive_oauth_state"


def _redirect_uri():
    return f"https://{request.host}/oauth/callback"


@app.route("/admin/drives/<drive_account_id>/connect-google")
def admin_connect_drive_google(drive_account_id):
    if not session.get("is_admin"):
        return redirect("/admin/login")

    drive_row = db.get_drive_account(drive_account_id)
    if not drive_row:
        abort(404)

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        abort(500, "GOOGLE_CLIENT_ID is not configured")

    state = secrets.token_urlsafe(32)
    session[OAUTH_STATE_SESSION_KEY] = {
        "token": state,
        "drive_account_id": str(drive_account_id),
    }

    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # Forces Google to hand back a refresh_token even on a reconnect -
        # without this, re-authorizing an already-granted app can come back
        # with no refresh_token at all.
        "prompt": "consent",
        "state": state,
    }
    return redirect(f"{AUTH_URL}?{urlencode(params)}")


@app.route("/oauth/callback")
def oauth_callback():
    state = request.args.get("state") or ""
    pending = session.pop(OAUTH_STATE_SESSION_KEY, None)
    if (
        not session.get("is_admin")
        or not pending
        or not state
        or not hmac.compare_digest(pending.get("token", ""), state)
    ):
        abort(400, "Estado OAuth ausente, inválido ou expirado")

    error = request.args.get("error")
    if error:
        return _result_page(
            ok=False,
            title="Autorização cancelada",
            message=f"O Google retornou: {error}.",
        )

    code = request.args.get("code")
    if not code:
        abort(400)

    drive_account_id = pending["drive_account_id"]
    drive_row = db.get_drive_account(drive_account_id)
    if not drive_row:
        abort(404)

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        abort(500, "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET is not configured")

    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        payload = resp.json()
    except requests.exceptions.RequestException as e:
        logger.error("Token exchange request failed: %s", e)
        return _result_page(
            ok=False,
            title="Falha ao conectar",
            message="Não foi possível falar com o Google agora. Tente novamente em instantes.",
        )

    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        logger.warning(
            "No refresh_token in token exchange response for drive_account %s: %s",
            drive_account_id, payload,
        )
        return _result_page(
            ok=False,
            title="Não recebi permissão permanente",
            message=(
                "O Google não devolveu um token permanente - isso acontece "
                "quando o acesso já tinha sido concedido antes. Revogue o "
                "acesso em myaccount.google.com/permissions (com a conta "
                "Google dessa pasta) e tente conectar de novo."
            ),
        )

    db.save_drive_account_refresh_token(drive_account_id, refresh_token)
    _drive_cache.pop(str(drive_account_id), None)
    logger.info("Drive account %s connected", drive_account_id)

    return _result_page(
        ok=True,
        title=f"{drive_row['label']} conectado!",
        message="Essa conta já está disponível no pool para novas contas de família.",
        link="/admin/drives",
        link_label="Voltar para as contas Drive",
    )


# --- family/viewer invite page (no Google OAuth here anymore) -------------

def _require_invited_user(invite_token):
    user_row = db.get_user_by_invite_token(invite_token)
    if not user_row or not db.is_effectively_active(user_row):
        abort(404)
    return user_row


@app.route("/connect/<invite_token>")
def connect_landing(invite_token):
    """Welcome page for a family account: no Google connection and no TMDB
    key needed here - both are admin-managed pools now. Just password setup
    and a link to the install instructions."""
    user_row = _require_invited_user(invite_token)
    has_password = bool(user_row.get("password_hash"))
    password_error = request.args.get("password_error")

    return render_template(
        "auth/invite.html",
        user=user_row,
        invite_token=invite_token,
        has_password=has_password,
        password_error=bool(password_error),
    )


def _result_page(ok, title, message, link=None, link_label=None):
    return render_template(
        "oauth/result.html",
        ok=ok,
        title=title,
        message=message,
        link=link,
        link_label=link_label,
    )
