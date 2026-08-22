"""JSON administrative API, for the Catálogo to drive this system.

Everything the /admin panel does, over HTTP with a service token instead
of a browser session. The Catálogo (Laravel, on the HostGator) becomes the
single place to manage families, Drive accounts and TMDB keys; this app
keeps only what it alone can do - OAuth with Google, searching the Drive,
ranking files and signing playback URLs.

Auth is `Authorization: Bearer <ADMIN_API_TOKEN>`, compared in constant
time. Deliberately not the admin password: that one belongs to a person
typing it into a login form, and a service credential that leaks should be
rotatable without locking the admin out of their own panel. With
ADMIN_API_TOKEN unset the whole API answers 503, so a deployment that
hasn't been configured yet is closed rather than open.

No CSRF here, on purpose - there is no cookie and no browser session to
ride on, so there is nothing for a third-party page to abuse. (The
before_request guard in sgd/admin.py only covers paths under /admin.)

The rules live in sgd/admin_actions.py, shared with the HTML panel, so the
two can't drift.
"""

import functools
import hmac
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from flask import jsonify, request

from sgd import admin_actions as actions
from sgd import app, db
from sgd.admin_actions import AdminActionError

logger = logging.getLogger(__name__)

API_PREFIX = "/api/admin"


# --- auth ---------------------------------------------------------------

def require_api_token(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        expected = os.environ.get("ADMIN_API_TOKEN")
        if not expected:
            return _error(
                "A API administrativa não está configurada neste ambiente "
                "(defina ADMIN_API_TOKEN).",
                503,
            )

        supplied = _bearer_token(request)
        if not supplied:
            # Distinguishing "sent nothing" from "sent something wrong"
            # gives an attacker nothing - they already know which one they
            # did. It does save the owner from reading "invalid credential"
            # after opening the URL in a browser, which is what happens on
            # every manual check.
            resp = _error(
                "Esta API exige um token de serviço no cabeçalho "
                "Authorization: Bearer. Abrir esta URL no navegador sempre "
                "cai aqui, e isso não indica problema.",
                401,
            )
            resp.headers["WWW-Authenticate"] = 'Bearer realm="stream-helium"'
            return resp

        if not hmac.compare_digest(expected, supplied):
            return _error("Credencial inválida.", 401)

        return view(*args, **kwargs)

    return wrapped


def _bearer_token(req) -> str:
    header = req.headers.get("Authorization", "")
    prefix = "Bearer "
    if header.startswith(prefix):
        return header[len(prefix):].strip()
    return ""


# --- responses ----------------------------------------------------------

def _error(message: str, status: int):
    resp = jsonify({"error": message})
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _ok(payload, status: int = 200):
    resp = jsonify(payload)
    resp.status_code = status
    # Administrative data is never worth caching, and some of it (invite
    # tokens) should not sit in an intermediary.
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _json_body() -> dict:
    """Accepts a JSON body, and falls back to form encoding so the API can
    be poked with curl -d during setup."""
    if request.is_json:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            return body
        return {}
    return request.form.to_dict()


def _as_bool(value, default=None):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "sim"}


def handle_action_errors(view):
    """Turns a rule saying no into the status it asked for, and anything
    unexpected into a 500 that says nothing about the internals."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except AdminActionError as e:
            return _error(e.message, e.status)
        except Exception:
            logger.exception("Unhandled error in admin API: %s", request.path)
            return _error("Erro interno ao processar a requisição.", 500)

    return wrapped


def endpoint(rule, **options):
    """Every API route is authenticated and translates domain errors."""

    def decorator(view):
        return app.route(API_PREFIX + rule, **options)(
            require_api_token(handle_action_errors(view))
        )

    return decorator


# --- serialization ------------------------------------------------------

def _plain(value):
    """psycopg hands back UUID, datetime and Decimal objects, none of which
    Flask's JSON encoder accepts. Dates go out as ISO 8601 so the Laravel
    side can parse them without guessing a format."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row(row: dict, drop=(), rename=None) -> dict:
    out = {}
    for key, value in dict(row).items():
        if key in drop:
            continue
        out[(rename or {}).get(key, key)] = _plain(value)
    return out


def _user_json(row: dict) -> dict:
    """`invite_token` is included: the Catálogo needs it to show the invite
    link. Credentials are not - password_hash never leaves this app, and
    the row already exposes only whether one exists."""
    data = _row(row, drop=(
        "password_hash", "google_refresh_token", "tmdb_api_key",
        # Cru demais para o painel: o estado consolidado vai em `device`.
        "device_label", "device_last_seen",
        "playback_started_at", "playback_last_seen",
    ))
    data["effectively_active"] = db.is_effectively_active(row)
    data["device"] = {k: _plain(v) for k, v in actions.device_state(row).items()}
    return data


def _drive_json(row: dict) -> dict:
    return _row(row, drop=("google_refresh_token",))


def _tmdb_json(row: dict) -> dict:
    """The key itself never goes over the wire - only the last four
    digits, which is all anyone needs to tell two keys apart."""
    from sgd.crypto import decrypt

    data = _row(row, drop=("api_key",))
    try:
        plain = decrypt(row["api_key"])
    except Exception:
        plain = None
    data["masked_key"] = _mask(plain)
    return data


def _mask(plain) -> str:
    if not plain:
        return "—"
    if len(plain) <= 4:
        return "•" * len(plain)
    return "•" * (len(plain) - 4) + plain[-4:]


# --- meta ---------------------------------------------------------------

@endpoint("/ping")
def api_ping():
    """Lets the Catálogo check the token and the connection in one call,
    without touching any data."""
    return _ok({"ok": True, "service": "stream-helium"})


@endpoint("/overview")
def api_overview():
    overview = db.get_admin_overview()
    return _ok({
        section: {k: _plain(v) for k, v in dict(values).items()}
        for section, values in overview.items()
    })


# --- family accounts ----------------------------------------------------

@endpoint("/users")
def api_list_users():
    return _ok({"users": [_user_json(row) for row in db.list_users()]})


@endpoint("/users", methods=["POST"])
def api_create_user():
    body = _json_body()
    chave = body.get("idempotency_key")

    # Antes de criar: esta mesma tentativa ja passou por aqui? Se sim, a
    # resposta e a conta que ela criou, e o status e 200 - nada foi criado
    # agora.
    if chave:
        try:
            ja = db.user_by_idempotency_key(str(chave).strip())
        except Exception as e:
            logger.warning("Idempotency lookup failed: %s", e)
            ja = None
        if ja:
            return _ok({"user": _user_json(ja), "reused": True}, 200)

    user = actions.create_user(
        email=body.get("email"),
        display_name=body.get("display_name"),
        expires_in_days=body.get("expires_in_days"),
        drive_account_id=body.get("drive_account_id"),
        idempotency_key=chave,
    )
    return _ok({"user": _user_json(user)}, 201)


@endpoint("/users/<uid>")
def api_get_user(uid):
    row = db.get_user(uid)
    if not row:
        return _error("Conta de família não encontrada.", 404)
    return _ok({"user": _user_json(row)})


@endpoint("/users/<uid>", methods=["PATCH", "POST"])
def api_update_user(uid):
    body = _json_body()
    user = actions.update_user(
        uid,
        email=body.get("email"),
        display_name=body.get("display_name"),
        expires_at=body.get("expires_at"),
        clear_expiration=_as_bool(body.get("clear_expiration"), False),
    )
    return _ok({"user": _user_json(user)})


@endpoint("/users/<uid>", methods=["DELETE"])
def api_delete_user(uid):
    actions.delete_user(uid)
    return _ok({"deleted": True})


@endpoint("/users/<uid>/active", methods=["PUT"])
def api_set_user_active(uid):
    """Explicit state instead of a toggle: the Catálogo may be showing a
    stale list, and a toggle would then do the opposite of what the person
    clicked. Omitting `active` falls back to toggling."""
    body = _json_body()
    desired = _as_bool(body.get("active"), None)
    user = actions.toggle_user(uid) if desired is None else actions.set_user_active(uid, desired)
    return _ok({"user": _user_json(user)})


@endpoint("/users/<uid>/renew", methods=["POST"])
def api_renew_user(uid):
    user = actions.renew_user(uid, _json_body().get("days", 30))
    return _ok({"user": _user_json(user)})


@endpoint("/users/<uid>/drive", methods=["PUT"])
def api_reassign_user(uid):
    """A drive_account_id pins the family to it; `auto` goes back to
    balancing across the pool."""
    body = _json_body()
    if _as_bool(body.get("auto"), False):
        user = actions.auto_assign_user(uid)
    else:
        user = actions.reassign_user(uid, body.get("drive_account_id"))
    return _ok({"user": _user_json(user)})


@endpoint("/users/<uid>/password", methods=["DELETE"])
def api_reset_password(uid):
    user = actions.reset_password(uid)
    return _ok({"user": _user_json(user)})


@endpoint("/users/<uid>/device", methods=["DELETE"])
def api_free_device(uid):
    actions.free_device(uid)
    return _ok({"freed": True})


# --- viewer credentials -------------------------------------------------
#
# O Catálogo hospeda o login e a página da conta; aqui ficam só as duas
# operações que dependem do que este sistema guarda. A senha em claro
# passa por aqui e não é gravada em lugar nenhum — vira hash na hora, e
# o hash nunca sai pela API.

@endpoint("/authenticate", methods=["POST"])
def api_authenticate():
    """Confere e-mail e senha de um espectador.

    Credencial errada e conta indisponível são casos diferentes, e voltam
    com status diferentes (401 e 403): dizer "senha incorreta" para quem
    digitou certo manda a pessoa tentar de novo para sempre.

    Sem limite de tentativas aqui de propósito — quem chama já precisa do
    token de serviço, e o formulário exposto ao público é o do Catálogo,
    que é onde a contagem faz sentido.
    """
    body = _json_body()
    user = actions.authenticate(body.get("email"), body.get("password"))
    return _ok({"user": _user_json(user)})


@endpoint("/invites/<token>")
def api_invite(token):
    """Resolve um convite para o Catálogo montar a página de boas-vindas."""
    user = actions.user_by_invite(token)
    return _ok({"user": _user_json(user)})


@endpoint("/users/<uid>/password", methods=["PUT"])
def api_set_password(uid):
    body = _json_body()
    user = actions.set_password(uid, body.get("password"))
    return _ok({"user": _user_json(user)})


# --- Drive account pool -------------------------------------------------

@endpoint("/drives")
def api_list_drives():
    return _ok({"drives": [_drive_json(row) for row in db.list_drive_accounts()]})


@endpoint("/drives", methods=["POST"])
def api_create_drive():
    drive = actions.create_drive(_json_body().get("label"))
    # Connecting to Google still has to happen in a browser, here - the
    # OAuth callback URI is registered against this domain.
    return _ok({
        "drive": _drive_json(drive),
        "connect_url": f"https://{request.host}/admin/drives",
    }, 201)


@endpoint("/drives/status")
def api_drive_status():
    """Espaco em disco e conexao de cada conta, ao vivo.

    Separado de /drives porque custa uma chamada ao Google por conta: a
    tela do Catalogo abre com a listagem barata e preenche isto depois.
    """
    estados = {}
    for row in db.list_drive_accounts():
        did = str(row["id"])
        estados[did] = actions.live_drive_status(did) if row["connected"] and row["active"] else None

    return _ok({"statuses": {k: (v or None) for k, v in estados.items()}})


@endpoint("/drives/<did>/active", methods=["PUT"])
def api_toggle_drive(did):
    return _ok({"drive": _drive_json(actions.toggle_drive(did))})


@endpoint("/drives/<did>", methods=["DELETE"])
def api_delete_drive(did):
    affected = actions.delete_drive(did)
    return _ok({"deleted": True, "redistributed_families": affected})


# --- TMDB key pool ------------------------------------------------------

@endpoint("/tmdb-keys")
def api_list_tmdb_keys():
    return _ok({"keys": [_tmdb_json(row) for row in db.list_tmdb_keys()]})


@endpoint("/tmdb-keys", methods=["POST"])
def api_create_tmdb_key():
    body = _json_body()
    key = actions.create_tmdb_key(body.get("label"), body.get("api_key"))
    return _ok({"key": _tmdb_json(key)}, 201)


@endpoint("/tmdb-keys/<tid>/active", methods=["PUT"])
def api_toggle_tmdb_key(tid):
    return _ok({"key": _tmdb_json(actions.toggle_tmdb_key(tid))})


@endpoint("/tmdb-keys/<tid>", methods=["DELETE"])
def api_delete_tmdb_key(tid):
    actions.delete_tmdb_key(tid)
    return _ok({"deleted": True})


# --- o que foi assistido ------------------------------------------------
#
# Alimenta os catalogos "mais assistidos" e "voce ainda nao viu" do
# Catalogo. O que se registra e a ABERTURA de um titulo, nao reproducao
# confirmada — ver o comentario da tabela em sgd/db.py.

@endpoint("/views/top")
def api_most_viewed():
    """Titulos mais abertos, somando todas as contas.

    Conta pessoas e nao aberturas: um titulo que alguem reabriu vinte
    vezes nao e mais popular que um que vinte pessoas abriram uma vez.
    """
    try:
        limite = min(200, max(1, int(request.args.get("limit", 60))))
    except (TypeError, ValueError):
        limite = 60

    return _ok({"titles": [
        {k: _plain(v) for k, v in dict(row).items()}
        for row in db.most_viewed_titles(limite)
    ]})


@endpoint("/views/user/<uid>")
def api_titles_seen(uid):
    """Ids que esta conta ja abriu, para o Catalogo montar o inverso."""
    if not db.get_user(uid):
        return _error("Conta nao encontrada.", 404)

    return _ok({"seen": db.titles_seen_by(uid)})


# --- settings -----------------------------------------------------------

@endpoint("/events")
def api_events():
    """Problemas de operacao dos ultimos dias, agregados.

    Existe porque quatro defeitos seguidos so foram descobertos quando
    alguem reclamou que o filme nao abria. Todos ja estavam no log; log de
    producao ninguem le todo dia. Aqui eles chegam ao painel.
    """
    try:
        dias = max(1, min(int(request.args.get("days", 7)), 60))
    except (TypeError, ValueError):
        dias = 7

    try:
        linhas = db.recent_events(dias)
    except Exception as e:
        # Nao ter o historico nao pode derrubar a tela que existe para
        # mostrar o que esta quebrado.
        logger.warning("Could not read ops events: %s", e)
        return _ok({"events": [], "degraded": True})

    return _ok({"events": [_row(dict(linha)) for linha in linhas]})


@endpoint("/settings")
def api_get_settings():
    return _ok({
        "proxy": {
            "enabled": actions.proxy_enabled(),
            # Without CF_PROXY_URL the toggle is inert, and the Catálogo
            # should say so instead of showing a switch that does nothing.
            "configured": bool(os.environ.get("CF_PROXY_URL")),
        },
    })


@endpoint("/settings/proxy", methods=["PUT"])
def api_set_proxy():
    desired = _as_bool(_json_body().get("enabled"), None)
    enabled = actions.toggle_proxy() if desired is None else actions.set_proxy_enabled(desired)
    return _ok({"proxy": {"enabled": enabled, "configured": bool(os.environ.get("CF_PROXY_URL"))}})
