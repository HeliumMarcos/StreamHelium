"""Google OAuth flow - admin-only now.

The admin connects each Drive account in the pool. Family (viewer) accounts
no longer go through Google OAuth at all - they're just assigned to one of
the pool accounts by the admin/system.

Requires a Google Cloud OAuth client of type **Web application** with this
exact redirect URI registered:

    https://<your-vercel-domain>/oauth/callback

Env vars required: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET.
"""

import logging
import os
from urllib.parse import urlencode

import requests
from flask import abort, redirect, request, Response, session

from sgd import app, db
from sgd.tenancy import _drive_cache

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"

# The OAuth `state` param round-trips through Google, so it's used to carry
# which drive_account this authorization is for. Prefixed so a stray/replayed
# state can't be confused with anything else.
STATE_PREFIX = "drive:"


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
        "state": f"{STATE_PREFIX}{drive_account_id}",
    }
    return redirect(f"{AUTH_URL}?{urlencode(params)}")


@app.route("/oauth/callback")
def oauth_callback():
    error = request.args.get("error")
    if error:
        return _result_page(
            ok=False,
            title="Autorização cancelada",
            message=f"O Google retornou: {error}.",
        )

    code = request.args.get("code")
    state = request.args.get("state") or ""
    if not code or not state.startswith(STATE_PREFIX):
        abort(400)

    drive_account_id = state[len(STATE_PREFIX):]
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
    """Welcome page for a family account: no Google connection needed here
    (that's admin-only, done once per pool account) - just an optional own
    TMDB key and a link to the install instructions."""
    user_row = _require_invited_user(invite_token)
    tmdb_connected = user_row["tmdb_api_key"] is not None
    tmdb_note = "Chave salva." if tmdb_connected else "Opcional - melhora títulos em pt-BR."

    html = f"""
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Bem-vindo - Stream Helium</title>
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;
            background:#0a0710;color:#ece8f4;font:16px/1.6 system-ui,sans-serif;
            text-align:center;padding:24px">
  <div style="max-width:28rem;width:100%">
    <h1>Bem-vindo(a){', ' + user_row['display_name'] if user_row.get('display_name') else ''}</h1>
    <p style="color:#a79fbb">Seu acesso já está pronto.</p>
    <a href="/u/{user_row['id']}/"
       style="display:inline-block;margin:1rem 0;padding:.6rem 1.2rem;border-radius:.5rem;
              background:#8b5cf6;color:#fff;text-decoration:none">Ver instruções de instalação</a>
    <hr style="border-color:#221a33;margin:1.5rem 0">
    <form method="post" action="/connect/{invite_token}/tmdb"
          style="display:flex;gap:.5rem;justify-content:center">
      <input type="text" name="tmdb_api_key" placeholder="Sua chave TMDB (opcional)"
             style="flex:1;padding:.5rem;border-radius:.4rem;border:1px solid #332844;
                    background:#120c1c;color:#ece8f4">
      <button type="submit" style="padding:.5rem 1rem;border-radius:.4rem;border:none;
              background:#22d3ee;color:#0a0710;font-weight:600">Salvar</button>
    </form>
    <p style="color:#a79fbb;font-size:.85rem">{tmdb_note}
      Obtenha a sua em
      <a href="https://www.themoviedb.org/settings/api" style="color:#8b5cf6">themoviedb.org</a>.
    </p>
  </div>
</div>
"""
    return Response(html, mimetype="text/html; charset=utf-8")


@app.route("/connect/<invite_token>/tmdb", methods=["POST"])
def connect_tmdb(invite_token):
    user_row = _require_invited_user(invite_token)
    tmdb_api_key = (request.form.get("tmdb_api_key") or "").strip() or None
    db.save_tmdb_key(user_row["id"], tmdb_api_key)
    return redirect(f"/connect/{invite_token}")


def _result_page(ok, title, message, link=None, link_label=None):
    color = "#34d399" if ok else "#fbbf24"
    link_html = (
        f'<p><a href="{link}" style="color:#8b5cf6">{link_label}</a></p>'
        if link else ""
    )
    html = f"""
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;
            background:#0a0710;color:#ece8f4;font:16px/1.6 system-ui,sans-serif;
            text-align:center;padding:24px">
  <div style="max-width:32rem">
    <h1 style="color:{color}">{title}</h1>
    <p style="color:#a79fbb">{message}</p>
    {link_html}
  </div>
</div>
"""
    return Response(html, mimetype="text/html; charset=utf-8")
