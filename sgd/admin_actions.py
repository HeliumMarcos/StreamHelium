"""Administrative rules, with no HTTP attached.

Every administrative action used to live inside a Flask view in
`sgd/admin.py`, mixing three things: reading a form, applying a rule, and
answering with a redirect plus a flash message. The Catálogo now needs to
drive the same actions over JSON (`sgd/admin_api.py`), and two copies of
"how to create a family account" would drift apart within a month.

So the rules live here, taking plain arguments and returning plain data.
The HTML panel and the JSON API are both thin layers on top: one renders a
flash and a redirect, the other renders a status code and a body.

Validation errors raise AdminActionError, which each layer turns into
whatever it speaks - a flash message or an HTTP status.
"""

import logging
import os
from datetime import datetime, timezone

from sgd import db

logger = logging.getLogger(__name__)


class AdminActionError(Exception):
    """A rule said no, in a way that is the caller's fault and worth
    explaining. `status` is the HTTP status the API should answer with;
    the HTML panel ignores it and just shows `message`."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class NotFound(AdminActionError):
    def __init__(self, message: str = "Registro não encontrado."):
        super().__init__(message, status=404)


# --- helpers ------------------------------------------------------------

def _clean_email(raw: str | None) -> str:
    email = (raw or "").strip().lower()
    if not email or "@" not in email:
        raise AdminActionError("Informe um endereço de e-mail válido.")
    return email


def _clean_optional(raw: str | None) -> str | None:
    return (raw or "").strip() or None


def _parse_date(raw: str | None) -> datetime | None:
    """Accepts the YYYY-MM-DD that both the date input and the JSON API
    send. Anything else is a caller mistake, not a server error."""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise AdminActionError("Informe uma data de expiração válida (AAAA-MM-DD).")


def _require_user(uid: str) -> dict:
    user_row = db.get_user(uid)
    if not user_row:
        raise NotFound("Conta de família não encontrada.")
    return user_row


def _forget_cached_drive(drive_account_id) -> None:
    """The Drive client is cached per pool account for the lifetime of a
    warm serverless instance. Any change to the assignment has to drop it,
    or the next request keeps talking to the old account."""
    from sgd.tenancy import _drive_cache
    _drive_cache.pop(str(drive_account_id), None)


# --- device and playback state ------------------------------------------

def _minutes_since(moment) -> float | None:
    if moment is None:
        return None
    return (datetime.now(timezone.utc) - moment).total_seconds() / 60


def device_state(user_row: dict) -> dict:
    """What device this family is on, and whether it is watching right now.

    Two independent signals, because they answer different questions:

    - `device_sessions` records a User-Agent the last time the account
      opened a title. It says *what* the person is using, and is the only
      signal at all when the Cloudflare proxy is off.
    - `playback_sessions` is fed by the Worker while bytes are actually
      flowing. It says whether a video is playing *now*, which the first
      one cannot: opening a menu and watching a film look the same to it.

    Both columns come from the JOIN in db.list_users(), so this costs no
    extra query per row.
    """
    device_idle = _minutes_since(user_row.get("device_last_seen"))
    playback_idle = _minutes_since(user_row.get("playback_last_seen"))

    device_ttl = int(os.environ.get("DEVICE_SESSION_TTL_MINUTES", "240"))
    playback_ttl = int(os.environ.get("PLAYBACK_IDLE_SECONDS", "180")) / 60

    playing = playback_idle is not None and playback_idle < playback_ttl
    known = device_idle is not None and device_idle < device_ttl

    return {
        "label": (user_row.get("device_label") or None) if known else None,
        "known": known,
        "idle_minutes": round(device_idle) if device_idle is not None else None,
        "last_seen": user_row.get("device_last_seen"),
        "playing": playing,
        "playing_since": user_row.get("playback_started_at") if playing else None,
        "playing_minutes": (
            round(_minutes_since(user_row["playback_started_at"]))
            if playing and user_row.get("playback_started_at")
            else None
        ),
    }


# --- family accounts ----------------------------------------------------

def create_user(
    email: str | None,
    display_name: str | None = None,
    expires_in_days=None,
    drive_account_id: str | None = None,
) -> dict:
    """Creates a family account, assigning a pool Drive account.

    An explicit choice pins the family to that Drive; leaving it empty
    balances across the pool and stays unpinned, so later rebalancing is
    free to move it.
    """
    email = _clean_email(email)
    display_name = _clean_optional(display_name)

    days = None
    if expires_in_days not in (None, ""):
        raw = str(expires_in_days).strip()
        if raw:
            if not raw.isdigit() or int(raw) < 1:
                raise AdminActionError("A validade em dias deve ser um número inteiro positivo.")
            days = int(raw)

    chosen = _clean_optional(drive_account_id)
    if chosen:
        assigned_drive_id, pinned = chosen, True
    else:
        auto = db.pick_least_loaded_drive_account()
        assigned_drive_id, pinned = (str(auto["id"]) if auto else None), False

    try:
        return db.create_user(
            email, display_name,
            drive_account_id=assigned_drive_id,
            expires_in_days=days,
            pinned=pinned,
        )
    except Exception as e:
        # The realistic cause is the UNIQUE on email; anything else is
        # still the caller's problem to see, and the detail is in the log.
        logger.warning("Failed to create user %s: %s", email, e)
        raise AdminActionError(
            "Não foi possível criar a conta. Verifique se o e-mail já está cadastrado.",
            status=409,
        )


def update_user(
    uid: str,
    email: str | None,
    display_name: str | None = None,
    expires_at: str | None = None,
    clear_expiration: bool = False,
) -> dict:
    _require_user(uid)
    email = _clean_email(email)
    display_name = _clean_optional(display_name)

    parsed = None if clear_expiration else _parse_date(expires_at)

    db.update_user(uid, email, display_name, parsed, clear_expiration=clear_expiration)
    return _require_user(uid)


def set_user_active(uid: str, active: bool) -> dict:
    _require_user(uid)
    db.set_active(uid, bool(active))
    return _require_user(uid)


def toggle_user(uid: str) -> dict:
    user_row = _require_user(uid)
    db.set_active(uid, not user_row["active"])
    return _require_user(uid)


def renew_user(uid: str, days=30) -> dict:
    _require_user(uid)
    raw = str(days if days not in (None, "") else 30).strip()
    if not raw.isdigit() or int(raw) < 1:
        raise AdminActionError("A renovação deve ser um número inteiro positivo de dias.")
    db.renew_user(uid, int(raw))
    return _require_user(uid)


def reassign_user(uid: str, drive_account_id: str | None) -> dict:
    """Pins the family to a specific pool account."""
    _require_user(uid)
    drive_account_id = _clean_optional(drive_account_id)
    if not drive_account_id:
        raise AdminActionError("Escolha uma conta Drive para fazer a atribuição.")
    if not db.get_drive_account(drive_account_id):
        raise NotFound("Conta Drive não encontrada.")

    db.reassign_drive_account(uid, drive_account_id, pinned=True)
    _forget_cached_drive(drive_account_id)
    return _require_user(uid)


def auto_assign_user(uid: str) -> dict:
    _require_user(uid)
    auto = db.pick_least_loaded_drive_account()
    db.reassign_drive_account(uid, str(auto["id"]) if auto else None, pinned=False)
    if auto:
        _forget_cached_drive(auto["id"])
    return _require_user(uid)


def reset_password(uid: str) -> dict:
    _require_user(uid)
    db.clear_password(uid)
    return _require_user(uid)


def free_device(uid: str) -> dict:
    """Clears both locks.

    Through the proxy the playback slot is the one that actually blocks
    anything, and it normally frees itself within PLAYBACK_IDLE_SECONDS -
    but if this is being called, something is stuck, and leaving half the
    state behind would look like the button did nothing.
    """
    user_row = _require_user(uid)
    db.clear_device_session(uid)
    try:
        db.clear_playback_session(uid)
    except Exception as e:
        logger.warning("Could not clear playback session for %s: %s", uid, e)
    return user_row


def delete_user(uid: str) -> None:
    _require_user(uid)
    db.delete_user(uid)


# --- Drive account pool -------------------------------------------------

def create_drive(label: str | None) -> dict:
    label = _clean_optional(label)
    if not label:
        raise AdminActionError("Informe um nome para a conta Drive.")
    return db.create_drive_account(label)


def toggle_drive(did: str) -> dict:
    drive_row = db.get_drive_account(did)
    if not drive_row:
        raise NotFound("Conta Drive não encontrada.")
    db.set_drive_account_active(did, not drive_row["active"])
    _forget_cached_drive(did)
    return db.get_drive_account(did)


def delete_drive(did: str) -> int:
    """Returns how many families were redistributed to the other pool
    accounts. Families left without any available Drive stay unassigned
    until a new one is connected."""
    if not db.get_drive_account(did):
        raise NotFound("Conta Drive não encontrada.")
    affected = db.redistribute_and_delete_drive_account(did)
    _forget_cached_drive(did)
    return affected


# --- TMDB key pool ------------------------------------------------------

def create_tmdb_key(label: str | None, api_key: str | None) -> dict:
    label = _clean_optional(label)
    api_key = _clean_optional(api_key)
    if not label or not api_key:
        raise AdminActionError("Informe o nome e a chave da API TMDB.")
    return db.create_tmdb_key(label, api_key)


def toggle_tmdb_key(tid: str) -> dict:
    key_row = db.get_tmdb_key(tid)
    if not key_row:
        raise NotFound("Chave TMDB não encontrada.")
    db.set_tmdb_key_active(tid, not key_row["active"])
    return db.get_tmdb_key(tid)


def delete_tmdb_key(tid: str) -> None:
    if not db.get_tmdb_key(tid):
        raise NotFound("Chave TMDB não encontrada.")
    db.delete_tmdb_key(tid)


# --- settings -----------------------------------------------------------

def proxy_enabled() -> bool:
    return db.get_setting("cf_proxy_enabled", default="1") != "0"


def set_proxy_enabled(enabled: bool) -> bool:
    db.set_setting("cf_proxy_enabled", "1" if enabled else "0")
    return bool(enabled)


def toggle_proxy() -> bool:
    return set_proxy_enabled(not proxy_enabled())
