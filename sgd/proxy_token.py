"""Short-lived access tokens for the Cloudflare Worker.

The Worker used to carry its own full copy of every pool account's OAuth
credentials, in its `ACCOUNTS` secret. That is the same refresh token the
addon already holds (encrypted) in Postgres - a second copy that nothing
keeps in sync. It drifted: re-authorizing a Drive account in /admin/drives
writes a fresh refresh token to the database and leaves the Worker's copy
behind, so the Worker's token refresh started failing with `invalid_grant`
and every proxied request became a 502, while playback with the proxy
turned off kept working off the database copy.

So the Worker now asks the addon for an access token instead of minting one
itself, and the refresh token lives in exactly one place. `ACCOUNTS` stays
supported as a fallback for a Worker that hasn't been pointed here yet.

The endpoint hands out a real Drive access token, so it is guarded by a
shared secret (`PROXY_SHARED_SECRET`, the same value configured on the
Worker) and never enabled implicitly - no secret set means the route
answers 503 rather than answering at all.

The same module also carries /internal/playback/<viewer_id>, the other
thing the Worker asks the addon while it serves a stream: whether this
viewer's single playback slot is theirs to keep.
"""

import hmac
import logging
import os
import re
from datetime import datetime

from flask import jsonify, request

from sgd import app, db, tenancy

logger = logging.getLogger(__name__)

VALID_ACCOUNT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Never hand out a token with less life left than this - the Worker caches
# what it gets, and a token that expires seconds later would have it
# re-fetching on every request.
MIN_LIFETIME_SECONDS = 60

# Nao controla mais acesso a nada - ninguem e barrado. Sobrou como o
# intervalo que o Worker recebe em `renew_after`, ou seja, de quanto em
# quanto tempo ele reavisa que a reproducao continua viva. E esse aviso
# que mantem "assistindo agora" atualizado no painel.
PLAYBACK_IDLE_SECONDS = int(os.environ.get("PLAYBACK_IDLE_SECONDS", "180"))


def _is_authorized(req):
    secret = os.environ.get("PROXY_SHARED_SECRET")
    if not secret:
        return None
    header = req.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer "):], secret)


def _seconds_until_expiry(drive):
    """How much life the cached access token has left. The Drive helper
    stores the absolute expiry, not a duration, so convert back."""
    expires = getattr(drive, "acc_token", None)
    expires = (expires.contents or {}).get("expires_in") if expires else None
    if isinstance(expires, str):
        try:
            expires = datetime.fromisoformat(expires)
        except ValueError:
            return None
    if not isinstance(expires, datetime):
        return None
    return int((expires - datetime.now()).total_seconds())


@app.route("/internal/drive-token/<account_id>")
def drive_token(account_id):
    authorized = _is_authorized(request)
    if authorized is None:
        return jsonify({"error": "proxy_token_endpoint_disabled"}), 503
    if not authorized:
        return jsonify({"error": "unauthorized"}), 401

    if not VALID_ACCOUNT_ID.match(account_id):
        return jsonify({"error": "unknown_account"}), 404

    drive = tenancy.drive_for_account(account_id)
    if drive is None:
        return jsonify({"error": "unknown_account"}), 404

    try:
        access_token = drive.get_acc_token()
    except Exception as e:
        logger.error("Drive token fetch failed for account %s: %s", account_id, e)
        db.record_event("drive_sem_token", account_id)
        return jsonify({"error": "token_refresh_failed"}), 502

    if not access_token:
        # get_acc_token() logs the actual OAuth error and returns None.
        logger.error("No access token available for drive account %s", account_id)
        # Na pratica isto e o invalid_grant: a autorizacao do Google foi
        # revogada e a conta precisa ser reconectada. Sem reproducao
        # nenhuma por aquele Drive ate alguem perceber.
        db.record_event("drive_sem_token", account_id)
        return jsonify({"error": "token_refresh_failed"}), 502

    expires_in = _seconds_until_expiry(drive)
    if expires_in is None or expires_in < MIN_LIFETIME_SECONDS:
        expires_in = MIN_LIFETIME_SECONDS

    response = jsonify({"access_token": access_token, "expires_in": expires_in})
    response.headers["Cache-Control"] = "no-store"
    return response


# The path parameter is deliberately not called user_id: routes.py has a
# before_request hook that treats any route with a user_id argument as a
# tenant-scoped one and loads the viewer plus a Drive client from the
# database. This endpoint needs neither, and gets called repeatedly
# during playback.
@app.route("/internal/playback/<viewer_id>", methods=["POST"])
def playback_claim(viewer_id):
    """Chamado pelo Worker enquanto alguem assiste.

    Ja foi a trava de um aparelho por vez, e devolvia 409 quando outra
    sessao estava com a vaga. O Worker traduzia isso em 403 e o player
    travava - inclusive quando a "outra sessao" era a propria pessoa
    abrindo o proximo episodio, porque um id de sessao novo nasce a cada
    listagem de streams.

    Agora sempre concede. O endpoint continua existindo por dois motivos:
    o Worker publicado segue chamando (nao precisa ser republicado para o
    bloqueio sumir), e o carimbo que ele atualiza e o que alimenta
    "assistindo agora" no painel."""
    authorized = _is_authorized(request)
    if authorized is None:
        return jsonify({"error": "proxy_token_endpoint_disabled"}), 503
    if not authorized:
        return jsonify({"error": "unauthorized"}), 401

    if not VALID_ACCOUNT_ID.match(viewer_id):
        return jsonify({"error": "unknown_user"}), 404

    session_id = (request.get_json(silent=True) or {}).get("session")
    if not session_id or not isinstance(session_id, str):
        return jsonify({"error": "missing_session"}), 400

    corpo = request.get_json(silent=True) or {}

    try:
        db.claim_playback_session(
            viewer_id, session_id, PLAYBACK_IDLE_SECONDS,
            file_id=(corpo.get("file_id") or None),
            # Cortado: nome de arquivo de video passa de 200 caracteres com
            # facilidade, e o painel mostra o comeco de qualquer jeito.
            file_name=((corpo.get("file_name") or "")[:300] or None),
        )
    except Exception as e:
        logger.warning("Playback record failed, allowing playback: %s", e)
        return jsonify({"granted": True, "degraded": True})

    return jsonify({"granted": True, "renew_after": PLAYBACK_IDLE_SECONDS // 3})
