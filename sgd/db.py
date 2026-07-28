"""Postgres access layer for multi-user accounts.

Expects a Vercel Postgres (Neon) database. Vercel injects the connection
string as POSTGRES_URL when you attach the Storage > Postgres integration to
the project; DATABASE_URL is accepted as a fallback for other Postgres
providers.
"""

import logging
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone

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
    """Idempotent - safe to call on every cold start."""
    with get_conn() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
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


# --- user CRUD -----------------------------------------------------------

def create_user(email: str, display_name: str | None = None) -> dict:
    invite_token = secrets.token_urlsafe(24)
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO users (email, display_name, invite_token)
            VALUES (%s, %s, %s)
            RETURNING id, email, display_name, active, invite_token,
                      created_at, connected_at,
                      (google_refresh_token IS NOT NULL) AS drive_connected
            """,
            (email, display_name, invite_token),
        ).fetchone()
    return row


def list_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, email, display_name, active, invite_token,
                   created_at, connected_at,
                   (google_refresh_token IS NOT NULL) AS drive_connected,
                   (tmdb_api_key IS NOT NULL) AS tmdb_connected
            FROM users
            ORDER BY created_at DESC
            """
        ).fetchall()
    return rows


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


def set_active(user_id: str, active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET active = %s WHERE id = %s", (active, user_id)
        )


def delete_user(user_id: str) -> None:
    """Removes the user row - revokes addon access immediately, since the
    manifest URL for that user stops resolving."""
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))


def save_google_refresh_token(user_id: str, refresh_token: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET google_refresh_token = %s, connected_at = %s
            WHERE id = %s
            """,
            (encrypt(refresh_token), datetime.now(timezone.utc), user_id),
        )


def save_tmdb_key(user_id: str, tmdb_api_key: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET tmdb_api_key = %s WHERE id = %s",
            (encrypt(tmdb_api_key) if tmdb_api_key else None, user_id),
        )


def decrypted_google_refresh_token(user_row: dict) -> str | None:
    return decrypt(user_row.get("google_refresh_token"))


def decrypted_tmdb_key(user_row: dict) -> str | None:
    return decrypt(user_row.get("tmdb_api_key"))
