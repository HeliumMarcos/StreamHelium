"""Per-request (multi-tenant) context.

Each family member has their own addon URL: /u/<user_id>/manifest.json,
/u/<user_id>/stream/... . Stremio has no login, so the user_id embedded in
the URL *is* the identity - treat it as a bearer credential.

Google Drive itself is no longer connected per family member. The admin
connects a small pool of Drive accounts (all pointing at the same shared
folder), and every family account is assigned to exactly one pool account.
"""

import logging
import os

from flask import abort, g

from sgd import db
from sgd.gdrive import GoogleDrive

logger = logging.getLogger(__name__)

# Reuses GoogleDrive instances across requests that land on the same warm
# serverless instance. Keyed by drive_account_id (the pool account), NOT by
# family user id - every family member sharing a pool account also shares
# its cached instance/token, which is exactly right since it's one Google
# identity underneath.
_drive_cache: dict[str, GoogleDrive] = {}


def _build_drive_instance(drive_row):
    refresh_token = db.decrypted_drive_refresh_token(drive_row)
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
    drive = GoogleDrive(token, cache_namespace=str(drive_row["id"]))
    drive.account_id = str(drive_row["id"])
    return drive


def _get_drive_instance(drive_account_id):
    drive = _drive_cache.get(drive_account_id)
    if drive is not None:
        return drive

    drive_row = db.get_drive_account(drive_account_id)
    if not drive_row or not drive_row["active"]:
        return None

    drive = _build_drive_instance(drive_row)
    if drive is None:
        return None

    _drive_cache[drive_account_id] = drive
    return drive


def load_tenant(user_id):
    """Called at the top of every /u/<user_id>/... route. Aborts the
    request (404) for unknown/inactive/expired/unassigned users rather than
    leaking which of those is the case."""
    user_row = db.get_user(user_id)
    if not user_row or not db.is_effectively_active(user_row):
        abort(404)

    drive_account_id = user_row.get("drive_account_id")
    if not drive_account_id:
        # Created but no pool account was available/assigned yet.
        abort(404)

    drive = _get_drive_instance(str(drive_account_id))
    if drive is None:
        abort(404)

    g.user = user_row
    g.gdrive = drive
    return user_row
