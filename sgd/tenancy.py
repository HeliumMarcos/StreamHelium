"""Per-user (multi-tenant) request context.

Each user gets their own addon URL: /u/<user_id>/manifest.json,
/u/<user_id>/stream/... . Stremio has no login, so the user_id embedded in
the URL *is* the identity - treat it as a bearer credential: it's a random
UUID, never enumerable, and deactivating/deleting the user immediately
breaks their URL.
"""

import logging
import os

from flask import abort, g

from sgd import db
from sgd.gdrive import GoogleDrive

logger = logging.getLogger(__name__)

# Reuses GoogleDrive instances (and their in-warm-container caches) across
# requests that land on the same warm serverless instance. Bounded loosely
# by process lifetime - Vercel recycles the container anyway.
_drive_cache: dict[str, GoogleDrive] = {}


def _build_drive_instance(user_row):
    refresh_token = db.decrypted_google_refresh_token(user_row)
    if not refresh_token:
        return None

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")

    token = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/drive.readonly"],
    }
    return GoogleDrive(token, cache_namespace=str(user_row["id"]))


def load_tenant(user_id):
    """Called at the top of every /u/<user_id>/... route. Aborts the
    request (404) for unknown/inactive/not-yet-connected users rather than
    leaking which of those three is the case."""
    user_row = db.get_user(user_id)
    if not user_row or not user_row["active"]:
        abort(404)

    drive = _drive_cache.get(str(user_row["id"]))
    if drive is None:
        drive = _build_drive_instance(user_row)
        if drive is None:
            # User exists but hasn't finished connecting Google Drive yet.
            abort(404)
        _drive_cache[str(user_row["id"])] = drive

    g.user = user_row
    g.gdrive = drive
    g.tmdb_api_key = db.decrypted_tmdb_key(user_row)
    return user_row
