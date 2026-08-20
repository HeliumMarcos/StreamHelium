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

# How long a playback session may go without the Worker checking in before
# another device is allowed to take the slot. Has to be comfortably longer
# than the Worker's own check-in interval, or a viewer would evict
# themselves. Also absorbs pauses: a paused player stops requesting bytes,
# so this is how long you can pause before someone else can take over.
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
        return jsonify({"error": "token_refresh_failed"}), 502

    if not access_token:
        # get_acc_token() logs the actual OAuth error and returns None.
        logger.error("No access token available for drive account %s", account_id)
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
    """Called by the Worker while a viewer streams, to keep or acquire that
    viewer's single playback slot.

    POST so it's plainly a mutation - it refreshes the slot's timestamp on
    every successful call, which is what lets an abandoned session expire
    on its own.

    409 means somebody else is watching. The Worker turns that into a 403
    for the player."""
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

    try:
        granted = db.claim_playback_session(
            viewer_id, session_id, PLAYBACK_IDLE_SECONDS
        )
    except Exception as e:
        # Fail open, same as the stream-listing device check: a database
        # hiccup shouldn't stop the household from watching anything.
        logger.warning("Playback claim failed, allowing playback: %s", e)
        return jsonify({"granted": True, "degraded": True})

    if not granted:
        return jsonify({"granted": False}), 409

    return jsonify({"granted": True, "renew_after": PLAYBACK_IDLE_SECONDS // 3})
