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
        # Chave de idempotencia do cadastro. O Catalogo escreve em dois
        # bancos que nenhuma transacao cobre: cria a conta aqui, define a
        # senha, e so entao grava o vinculo no MySQL. Falhando no meio,
        # sobrava usuario aqui sem vinculo la, e repetir dava e-mail
        # duplicado - a pessoa ficava sem conta e sem saida.
        #
        # Com a chave, repetir devolve a MESMA conta em vez de erro, e a
        # tentativa seguinte completa o que faltou.
        conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS idempotency_key TEXT;"
        )
        # Identificador do addon, trocavel.
        #
        # Ate agora o endereco do addon carregava o `id` da conta, que e
        # imutavel: trocar o link no Catalogo nao revogava nada, porque o
        # endereco da Vercel continuava valendo para sempre. Quem tivesse
        # visto o 302 uma vez assistia indefinidamente.
        #
        # NAO e preenchido para contas antigas de proposito. Enquanto uma
        # conta nao tem token, o `id` continua funcionando - senao publicar
        # isto derrubaria todo mundo antes de o Catalogo saber os tokens
        # novos. A conta migra quando alguem rotaciona o link dela.
        conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS stream_token TEXT;"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS users_stream_token_idx "
            "ON users (stream_token) WHERE stream_token IS NOT NULL;"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS users_idempotency_key_idx "
            "ON users (idempotency_key) WHERE idempotency_key IS NOT NULL;"
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

        # One row per viewer, tracking which playback session is currently
        # streaming. Distinct from device_sessions: that one is keyed on a
        # guessed fingerprint and refreshed when a stream list is fetched,
        # this one is keyed on a session id the addon issued and refreshed
        # by the Worker while bytes are actually flowing.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS playback_sessions (
                user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                session_id TEXT NOT NULL,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )

        # Que titulos cada conta abriu, para o Catalogo montar "mais
        # assistidos" e "voce ainda nao viu".
        #
        # Registra ABERTURA, nao reproducao confirmada: o sinal e a
        # requisicao de streams, que acontece quando alguem abre um titulo
        # no Stremio. Saber se o video realmente tocou exigiria carregar o
        # id do titulo pela URL assinada ate o Worker e de volta — mexer de
        # novo no caminho que serve o video, por uma precisao que nao muda
        # nenhuma decisao aqui: ninguem abre um titulo que nao pretende
        # ver.
        #
        # Guarda o minimo: quem, o que, quantas vezes, e quando foi a
        # primeira e a ultima. Sem IP, sem aparelho, sem historico
        # detalhado.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS title_views (
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                imdb_id TEXT NOT NULL,
                views INTEGER NOT NULL DEFAULT 1,
                first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, imdb_id)
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_title_views_imdb ON title_views (imdb_id);"
        )

        # Problemas de operacao, agregados por dia.
        #
        # Existe porque quatro defeitos seguidos passaram despercebidos ate
        # alguem reclamar que "o filme nao abre": o firewall da hospedagem
        # recusando toda consulta ao Catalogo, um crash em serie sem
        # episodio, imagens 404 e tokens do Google revogados. Todos ja
        # apareciam no log; ninguem le log de producao todo dia.
        #
        # Agregado e nao uma linha por ocorrencia: o 406 acontecia a CADA
        # reproducao, e uma tabela linha-a-linha viraria enxurrada. Aqui o
        # mesmo problema no mesmo dia soma no contador.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ops_events (
                kind TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                day DATE NOT NULL DEFAULT CURRENT_DATE,
                count INTEGER NOT NULL DEFAULT 1,
                first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (kind, detail, day)
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ops_events_day ON ops_events (day DESC);"
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

USER_COLUMNS = """id, email, display_name, active, invite_token,
                  drive_account_id, drive_account_pinned, expires_at,
                  created_at, connected_at, stream_token,
                  (tmdb_api_key IS NOT NULL) AS tmdb_connected"""


def user_by_idempotency_key(key: str) -> dict | None:
    """A conta ja criada por esta mesma tentativa de cadastro, se houver."""
    with get_conn() as conn:
        return conn.execute(
            f"SELECT {USER_COLUMNS} FROM users WHERE idempotency_key = %s",
            (key,),
        ).fetchone()


def create_user(
    email: str,
    display_name: str | None = None,
    drive_account_id: str | None = None,
    expires_in_days: int | None = None,
    pinned: bool = False,
    idempotency_key: str | None = None,
) -> dict:
    invite_token = secrets.token_urlsafe(24)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        if expires_in_days else None
    )
    if idempotency_key:
        # Repetir a mesma tentativa devolve a mesma conta, nao um erro.
        ja = user_by_idempotency_key(idempotency_key)
        if ja:
            return ja

    with get_conn() as conn:
        return conn.execute(
            f"""
            INSERT INTO users (email, display_name, invite_token,
                                drive_account_id, expires_at,
                                drive_account_pinned, idempotency_key,
                                stream_token)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {USER_COLUMNS}
            """,
            (email, display_name, invite_token, drive_account_id, expires_at,
             pinned and drive_account_id is not None, idempotency_key,
             new_stream_token()),
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
                   da.label AS drive_label,
                   ds.device_label, ds.last_seen AS device_last_seen,
                   ps.started_at AS playback_started_at,
                   ps.last_seen AS playback_last_seen
            FROM users u
            LEFT JOIN drive_accounts da ON da.id = u.drive_account_id
            LEFT JOIN device_sessions ds ON ds.user_id = u.id
            LEFT JOIN playback_sessions ps ON ps.user_id = u.id
            ORDER BY u.created_at DESC
            """
        ).fetchall()


def get_user(user_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = %s", (user_id,)
        ).fetchone()


def get_user_by_stream_token(token: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE stream_token = %s", (token,)
        ).fetchone()


def new_stream_token() -> str:
    """Aleatorio e opaco. Nao deriva do id, senao trocar nao trocaria nada."""
    return secrets.token_urlsafe(24)


def rotate_stream_token(user_id: str) -> str | None:
    """Da a conta um endereco novo e invalida o anterior no mesmo instante.

    Devolve o token novo, ou None se a conta nao existe.
    """
    token = new_stream_token()
    with get_conn() as conn:
        linha = conn.execute(
            "UPDATE users SET stream_token = %s WHERE id = %s RETURNING stream_token",
            (token, user_id),
        ).fetchone()
    return linha["stream_token"] if linha else None


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


def update_user(
    user_id: str,
    email: str,
    display_name: str | None,
    expires_at: datetime | None,
    clear_expiration: bool = False,
) -> None:
    """Direct edit of an existing family account - unlike renew_user() this
    sets an exact expiration (or None for no expiration at all), not a
    relative +N days."""
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET email = %s, display_name = %s,
                expires_at = %s
            WHERE id = %s
            """,
            (email, display_name, None if clear_expiration else expires_at, user_id),
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
    user_id: str, fingerprint: str, device_label: str, ttl_minutes: int = 0
) -> bool:
    """Anota qual aparelho esta conta esta usando agora.

    Ja recusou: um aparelho diferente dentro do TTL levava False, e o
    add-on devolvia um aviso no lugar dos streams. Nao recusa mais - o
    limite de um aparelho por vez saiu inteiro, porque prendia a propria
    pessoa. A impressao digital e aproximada (o Stremio nao expoe id de
    aparelho), entao trocar de rede ou atualizar o app ja bastava para
    parecer outro aparelho e travar ate o TTL vencer.

    O registro continua: e dele que sai "aparelho conectado" na pagina da
    conta e no painel. `ttl_minutes` sobrou por compatibilidade de
    assinatura; nada mais le esse valor.

    Devolve sempre True."""
    with get_conn() as conn:
        now = datetime.now(timezone.utc)

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


def claim_playback_session(user_id: str, session_id: str, idle_seconds: int) -> bool:
    """Called by the Worker while a viewer is actually streaming bytes.

    Ja recusou, e era esse o problema relatado: um `session_id` novo nasce
    a CADA listagem de streams, mas a vaga so era liberada apos
    `idle_seconds` de silencio. A pessoa competia consigo mesma - abrir o
    proximo episodio, ou o player reabrir a lista no meio do filme, criava
    uma sessao que a anterior bloqueava por tres minutos. O Worker
    traduzia o 409 em 403 e o player travava.

    Agora sempre concede e so atualiza o carimbo. O que sobra e presenca:
    quem esta assistindo e desde quando, que e o que o painel mostra.

    `idle_seconds` sobrou por compatibilidade; nada mais le esse valor."""
    with get_conn() as conn:
        now = datetime.now(timezone.utc)

        conn.execute(
            """
            INSERT INTO playback_sessions (user_id, session_id, last_seen)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                session_id = EXCLUDED.session_id,
                last_seen = EXCLUDED.last_seen,
                started_at = CASE
                    WHEN playback_sessions.session_id = EXCLUDED.session_id
                    THEN playback_sessions.started_at
                    ELSE EXCLUDED.last_seen
                END
            """,
            (user_id, session_id, now),
        )
        return True


def get_playback_session(user_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM playback_sessions WHERE user_id = %s", (user_id,)
        ).fetchone()


def clear_playback_session(user_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM playback_sessions WHERE user_id = %s", (user_id,))


# --- generic settings (key/value) ------------------------------------------

def record_title_view(user_id: str, imdb_id: str) -> None:
    """Marca que esta conta abriu este titulo.

    Nunca levanta: contabilizar uma visualizacao nao vale interromper uma
    reproducao. Um numero errado no catalogo de mais assistidos e menos
    grave que um filme que nao abre.
    """
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO title_views (user_id, imdb_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id, imdb_id) DO UPDATE
                    SET views = title_views.views + 1,
                        last_seen = now()
                """,
                (user_id, imdb_id),
            )
    except Exception as e:
        logger.warning("Could not record view of %s by %s: %s", imdb_id, user_id, e)


def most_viewed_titles(limit: int = 60) -> list[dict]:
    """Titulos mais abertos, somando todas as contas.

    Conta PESSOAS e nao aberturas: um titulo que uma pessoa reabriu vinte
    vezes nao e mais popular que um que vinte pessoas abriram uma vez.
    """
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT imdb_id,
                   COUNT(DISTINCT user_id) AS viewers,
                   SUM(views) AS opens,
                   MAX(last_seen) AS last_seen
            FROM title_views
            GROUP BY imdb_id
            ORDER BY viewers DESC, opens DESC, last_seen DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()


def titles_seen_by(user_id: str) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT imdb_id FROM title_views WHERE user_id = %s", (user_id,)
        ).fetchall()

    return [r["imdb_id"] for r in rows]


def get_setting(key: str, default: str | None = None) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = %s", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            (key, value),
        )

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


# --- eventos de operacao -------------------------------------------------

def record_event(kind: str, detail: str = "") -> None:
    """Anota um problema operacional. Nunca levanta.

    Registrar que algo deu errado nao pode ser mais uma coisa dando errado
    - especialmente porque os pontos que chamam isto ja estao no meio de
    um caminho degradado.
    """
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO ops_events (kind, detail)
                VALUES (%s, %s)
                ON CONFLICT (kind, detail, day) DO UPDATE
                    SET count = ops_events.count + 1,
                        last_seen = now()
                """,
                (kind[:60], (detail or "")[:200]),
            )
    except Exception as e:
        logger.warning("Could not record ops event %s: %s", kind, e)


def recent_events(days: int = 7, limit: int = 50) -> list[dict]:
    """Problemas dos ultimos dias, do mais recente para o mais antigo."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT kind, detail, day, count, first_seen, last_seen
            FROM ops_events
            WHERE day >= CURRENT_DATE - %s::int
            ORDER BY last_seen DESC
            LIMIT %s
            """,
            (days, limit),
        ).fetchall()


def forget_events_older_than(days: int = 60) -> None:
    """Retencao. Sem isto a tabela so cresce."""
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM ops_events WHERE day < CURRENT_DATE - %s::int", (days,))
    except Exception as e:
        logger.warning("Could not prune ops events: %s", e)
