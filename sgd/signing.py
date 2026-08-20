"""Signed playback URLs.

A proxied playback URL used to be a bare path: anyone holding the text
could stream that file forever, from anywhere, and the Worker had no idea
which viewer the bytes were for. Signing fixes both halves of that.

The addon appends four query parameters:

    u  viewer's user id
    n  playback session id - a fresh random value per stream listing, so
       two devices asking for the same file get different URLs
    e  expiry, unix seconds
    s  HMAC-SHA256 over the account, file, user, session and expiry,
       keyed with PROXY_SHARED_SECRET (the same secret the Worker already
       uses to fetch Drive tokens)

The Worker recomputes `s` and refuses anything that doesn't match or has
expired. `n` is what makes a per-viewer concurrency lock possible at all:
it is an identity the addon issued, not one sniffed from a User-Agent and
an IP, so it survives an app update and a change of network.

The file name in the path is deliberately not signed - the Worker ignores
it, it exists only so players show a sensible title, and including it
would make the signature sensitive to URL-encoding differences between
clients.
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

logger = logging.getLogger(__name__)

# How long a playback URL stays valid. Long enough to cover starting a
# film hours after browsing, and to not expire mid-playback on a long
# watch, while still putting a bound on a leaked link.
DEFAULT_TTL_SECONDS = 24 * 60 * 60


def _secret():
    return os.environ.get("PROXY_SHARED_SECRET")


def new_session_id():
    """One per stream listing. Short - it travels in every playback URL."""
    return secrets.token_urlsafe(9)


def _payload(account_id, file_id, user_id, session_id, expires_at):
    return f"{account_id}:{file_id}:{user_id}:{session_id}:{expires_at}"


def _digest(secret, payload):
    raw = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign(account_id, file_id, user_id, session_id, ttl_seconds=None):
    """Returns the query string to append to a playback URL, or None when
    signing isn't configured - in which case the caller emits the old
    unsigned URL and the Worker keeps accepting it (see REQUIRE_SIGNED_URLS
    on the Worker side)."""
    secret = _secret()
    if not secret:
        return None

    ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_TTL_SECONDS
    expires_at = int(time.time()) + ttl
    signature = _digest(
        secret, _payload(account_id, file_id, user_id, session_id, expires_at)
    )
    return f"u={user_id}&n={session_id}&e={expires_at}&s={signature}"


def verify(account_id, file_id, user_id, session_id, expires_at, signature, now=None):
    """Mirror of `sign`, kept here so the tests exercise the real thing.
    The Worker has its own copy of this check in JavaScript - if you change
    the payload format, change it in both."""
    secret = _secret()
    if not secret:
        return False
    try:
        expires_at = int(expires_at)
    except (TypeError, ValueError):
        return False
    if expires_at < (now if now is not None else time.time()):
        return False

    expected = _digest(
        secret, _payload(account_id, file_id, user_id, session_id, expires_at)
    )
    return hmac.compare_digest(expected, signature or "")
