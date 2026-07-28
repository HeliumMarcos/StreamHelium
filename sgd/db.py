"""Postgres access layer.

Two kinds of accounts, deliberately separate:
- drive_accounts: Google Drive "source" accounts, connected by the admin.
  All of them point at the same shared folder (per the current setup).
- users: viewer accounts (family members), created by the admin via a
  single-use invite link, each assigned to exactly one drive_account.

Expects a Vercel Postgres (Neon) database. Vercel injects the connection
string as POSTGRES_URL when you attach the Storage > Postgres integration to
the project; DATABASE_URL is accepted as a fallback for other Postgres
providers.
"""

import logging
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from sgd.crypto import decrypt, encrypt

logger = logging.getLogger(__name__)

DB_URL = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")


@contextmanager
def get_conn():
    if not DB_URL:
        raise RuntimeError(
            "POSTGRES_URL (or DATABASE_URL) is not set. Attach a Postgres "
            "database to the Vercel project (Storage tab) or set the env "
            "var manually."
        )
    conn = psycopg.connect(DB_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema():
    """Idempotent - safe to call on every cold start, and safe to run
    against a database that already has the single-tenant-OAuth version of
    the `users` table (the ALTERs below are additive)."""
    with get_conn() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drive_accounts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                label TEXT NOT NULL,
                google_refresh_token TEXT,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                connected_at TIMESTAMPTZ
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email TEXT NOT NULL UNIQUE,
                display_name TEXT,
                google_refresh_token TEXT,
                tmdb_api_key TEXT,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                invite_token TEXT UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                connected_at TIMESTAMPTZ
            );
            """
        )
        # Additive migration for databases created before the pool model.
        conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS drive_account_id "
            "UUID REFERENCES drive_accounts(id) ON DELETE SET NULL;"
        )
        conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;"
        )
        conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS drive_account_pinned "
            "BOOLEAN NOT NULL DEFAULT FALSE;"
        )
        conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tmdb_keys (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                label TEXT NOT NULL,
                api_key TEXT NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_sessions (
                user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                fingerprint TEXT NOT NULL,
                device_label TEXT,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )


def _is_expired(user_row: dict) -> bool:
    expires_at = user_row.get("expires_at")
    return expires_at is not None and expires_at <= datetime.now(timezone.utc)


def is_effectively_active(user_row: dict) -> bool:
    """active flag AND not past its expiration date, if any."""
    return bool(user_row.get("active")) and not _is_expired(user_row)


# --- drive_accounts (the pool, admin-managed) -----------------------------

def create_drive_account(label: str) -> dict:
    with get_conn() as conn:
        return conn.execute(
            """
            INSERT INTO drive_accounts (label)
            VALUES (%s)
            RETURNING id, label, active, created_at, connected_at,
                      (google_refresh_token IS NOT NULL) AS connected
            """,
            (label,),
        ).fetchone()


def list_drive_accounts() -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT da.id, da.label, da.active, da.created_at, da.connected_at,
                   (da.google_refresh_token IS NOT NULL) AS connected,
                   COUNT(u.id) FILTER (WHERE u.active) AS assigned_count
            FROM drive_accounts da
            LEFT JOIN users u ON u.drive_account_id = da.id
            GROUP BY da.id
            ORDER BY da.created_at ASC
            """
        ).fetchall()


def get_drive_account(drive_account_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM drive_accounts WHERE id = %s", (drive_account_id,)
        ).fetchone()


def set_drive_account_active(drive_account_id: str, active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE drive_accounts SET active = %s WHERE id = %s",
            (active, drive_account_id),
        )


def delete_drive_account(drive_account_id: str) -> None:
    """Removes the pool account with no redistribution - kept for cases
    where you explicitly want the affected family accounts to go idle
    instead of silently landing on a different Drive. Prefer
    redistribute_and_delete_drive_account() for the normal "remove and
    rebalance" flow."""
    with get_conn() as conn:
        conn.execute("DELETE FROM drive_accounts WHERE id = %s", (drive_account_id,))


def redistribute_and_delete_drive_account(drive_account_id: str) -> int:
    """Reassigns every family account pointed at this pool account to
    whichever OTHER active/connected pool account currently has the fewest
    people, one at a time (so the split stays even - each reassignment
    updates the count the next one sees), then deletes the account. If no
    other pool account is available, affected family accounts end up
    unassigned (their addon 404s until reassigned) rather than silently
    picking something. Returns how many family accounts were affected."""
    with get_conn() as conn:
        affected = conn.execute(
            "SELECT id FROM users WHERE drive_account_id = %s",
            (drive_account_id,),
        ).fetchall()

        for row in affected:
            replacement = conn.execute(
                """
                SELECT da.id
                FROM drive_accounts da
                LEFT JOIN users u ON u.drive_account_id = da.id AND u.active
                WHERE da.active AND da.google_refresh_token IS NOT NULL
                  AND da.id != %s
                GROUP BY da.id
                ORDER BY COUNT(u.id) ASC, da.created_at ASC
                LIMIT 1
                """,
                (drive_account_id,),
            ).fetchone()
            new_drive_id = replacement["id"] if replacement else None
            conn.execute(
                "UPDATE users SET drive_account_id = %s, drive_account_pinned = FALSE "
                "WHERE id = %s",
                (new_drive_id, row["id"]),
            )

        conn.execute("DELETE FROM drive_accounts WHERE id = %s", (drive_account_id,))
        return len(affected)


def save_drive_account_refresh_token(drive_account_id: str, refresh_token: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE drive_accounts
            SET google_refresh_token = %s, connected_at = %s
            WHERE id = %s
            """,
            (encrypt(refresh_token), datetime.now(timezone.utc), drive_account_id),
        )


def decrypted_drive_refresh_token(drive_row: dict) -> str | None:
    return decrypt(drive_row.get("google_refresh_token"))


def pick_least_loaded_drive_account() -> dict | None:
    """The account to assign the NEXT new family user to: whichever
    connected, active drive_account currently has the fewest active family
    users pointed at it. This is what produces the even split you described
    (10 family / 2 drives -> 5 each; 9 family / 3 drives -> 3 each) and
    keeps self-balancing as accounts are added/removed later, instead of a
    fixed round-robin index that would drift."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT da.id, da.label
            FROM drive_accounts da
            LEFT JOIN users u ON u.drive_account_id = da.id AND u.active
            WHERE da.active AND da.google_refresh_token IS NOT NULL
            GROUP BY da.id
            ORDER BY COUNT(u.id) ASC, da.created_at ASC
            LIMIT 1
            """
        ).fetchone()


# --- users (family/viewer accounts) ---------------------------------------

def create_user(
    email: str,
    display_name: str | None = None,
    drive_account_id: str | None = None,
    expires_in_days: int | None = None,
    pinned: bool = False,
) -> dict:
    invite_token = secrets.token_urlsafe(24)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        if expires_in_days else None
    )
    with get_conn() as conn:
        return conn.execute(
            """
            INSERT INTO users (email, display_name, invite_token,
                                drive_account_id, expires_at, drive_account_pinned)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, email, display_name, active, invite_token,
                      drive_account_id, drive_account_pinned, expires_at,
                      created_at, connected_at,
                      (tmdb_api_key IS NOT NULL) AS tmdb_connected
            """,
            (email, display_name, invite_token, drive_account_id, expires_at,
             pinned and drive_account_id is not None),
        ).fetchone()


def list_users() -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT u.id, u.email, u.display_name, u.active, u.invite_token,
                   u.drive_account_id, u.drive_account_pinned, u.expires_at,
                   u.created_at, u.connected_at,
                   (u.tmdb_api_key IS NOT NULL) AS tmdb_connected,
                   (u.password_hash IS NOT NULL) AS has_password,
                   da.label AS drive_label
            FROM users u
            LEFT JOIN drive_accounts da ON da.id = u.drive_account_id
            ORDER BY u.created_at DESC
            """
        ).fetchall()


def get_user(user_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = %s", (user_id,)
        ).fetchone()


def get_user_by_invite_token(token: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE invite_token = %s", (token,)
        ).fetchone()


def get_user_by_email(email: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = %s", (email.strip().lower(),)
        ).fetchone()


def set_password(user_id: str, password_hash: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (password_hash, user_id),
        )


def clear_password(user_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = NULL WHERE id = %s", (user_id,)
        )


def set_active(user_id: str, active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET active = %s WHERE id = %s", (active, user_id)
        )


def renew_user(user_id: str, days: int) -> None:
    """Resets the expiration to `days` from now (not additive on top of the
    old date) - matches "renovei por mais 30 dias" rather than stacking."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET expires_at = %s WHERE id = %s",
            (datetime.now(timezone.utc) + timedelta(days=days), user_id),
        )


def clear_expiration(user_id: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET expires_at = NULL WHERE id = %s", (user_id,))


def reassign_drive_account(
    user_id: str, drive_account_id: str | None, pinned: bool = False
) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET drive_account_id = %s, drive_account_pinned = %s "
            "WHERE id = %s",
            (drive_account_id, pinned and drive_account_id is not None, user_id),
        )


def delete_user(user_id: str) -> None:
    """Removes the user row - revokes addon access immediately, since the
    manifest URL for that user stops resolving."""
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))


def save_tmdb_key(user_id: str, tmdb_api_key: str | None) -> None:
    """Deprecated: TMDB keys are now admin-managed in the tmdb_keys pool,
    not per family account. Kept only so old rows with a value already set
    don't error on read; do not call this for new writes."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET tmdb_api_key = %s WHERE id = %s",
            (encrypt(tmdb_api_key) if tmdb_api_key else None, user_id),
        )


# --- tmdb_keys (pool, admin-managed) ---------------------------------------

def create_tmdb_key(label: str, api_key: str) -> dict:
    with get_conn() as conn:
        return conn.execute(
            """
            INSERT INTO tmdb_keys (label, api_key)
            VALUES (%s, %s)
            RETURNING id, label, active, created_at
            """,
            (label, encrypt(api_key)),
        ).fetchone()


def list_tmdb_keys() -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, label, active, created_at, api_key FROM tmdb_keys "
            "ORDER BY created_at ASC"
        ).fetchall()


def get_tmdb_key(tmdb_key_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM tmdb_keys WHERE id = %s", (tmdb_key_id,)
        ).fetchone()


def set_tmdb_key_active(tmdb_key_id: str, active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE tmdb_keys SET active = %s WHERE id = %s", (active, tmdb_key_id)
        )


def delete_tmdb_key(tmdb_key_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM tmdb_keys WHERE id = %s", (tmdb_key_id,))


def list_active_tmdb_keys_decrypted() -> list[str]:
    """Used by sgd/meta.py to pick a key per request - returns plain values,
    not rows, since that's all the caller needs and it avoids leaking
    ciphertext/ids into a hot path."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT api_key FROM tmdb_keys WHERE active = TRUE"
        ).fetchall()
    return [decrypt(r["api_key"]) for r in rows if r["api_key"]]


def has_active_tmdb_key() -> bool:
    """Cheap existence check for status pages - doesn't decrypt anything."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM tmdb_keys WHERE active = TRUE LIMIT 1"
        ).fetchone()
    return row is not None


# --- device_sessions (one active device per family account) ---------------

def touch_device_session(
    user_id: str, fingerprint: str, device_label: str, ttl_minutes: int
) -> bool:
    """Registers this request's device as the active one for this family
    account, UNLESS a different device is already active and within its TTL
    - in which case this call is rejected (returns False) and nothing is
    written. The device fingerprint is a best-effort signature (see
    routes.py) - there's no real device ID available from Stremio's HTTP
    API, so this is approximate, not cryptographically exact."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT fingerprint, last_seen FROM device_sessions "
            "WHERE user_id = %s FOR UPDATE",
            (user_id,),
        ).fetchone()
        now = datetime.now(timezone.utc)

        if row and row["fingerprint"] != fingerprint:
            age_minutes = (now - row["last_seen"]).total_seconds() / 60
            if age_minutes < ttl_minutes:
                return False

        conn.execute(
            """
            INSERT INTO device_sessions (user_id, fingerprint, device_label, last_seen)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                fingerprint = EXCLUDED.fingerprint,
                device_label = EXCLUDED.device_label,
                last_seen = EXCLUDED.last_seen
            """,
            (user_id, fingerprint, device_label, now),
        )
        return True


def get_device_session(user_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM device_sessions WHERE user_id = %s", (user_id,)
        ).fetchone()


def clear_device_session(user_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM device_sessions WHERE user_id = %s", (user_id,))


# --- overview stats for the admin dashboard ---------------------------------

def get_admin_overview() -> dict:
    with get_conn() as conn:
        family = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE active AND (expires_at IS NULL OR expires_at > now())) AS active_count,
                COUNT(*) FILTER (WHERE active AND expires_at IS NOT NULL AND expires_at <= now()) AS expired_count,
                COUNT(*) FILTER (WHERE NOT active) AS disabled_count
            FROM users
            """
        ).fetchone()
        drives = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE active AND google_refresh_token IS NOT NULL) AS connected
            FROM drive_accounts
            """
        ).fetchone()
        tmdb = conn.execute(
            "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE active) AS active_count FROM tmdb_keys"
        ).fetchone()
        devices = conn.execute(
            "SELECT COUNT(*) AS total FROM device_sessions WHERE last_seen > now() - interval '240 minutes'"
        ).fetchone()

    return {"family": family, "drives": drives, "tmdb": tmdb, "devices": devices}
