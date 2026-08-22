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

import psycopg
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


# --- viewer credentials -------------------------------------------------

# Mesmo minimo que o formulario antigo do proprio Stream Helium usava.
# Curto de proposito: quem instala isso e familia, nao empresa, e uma
# regra severa demais so produz senha anotada em papel.
MIN_PASSWORD_LENGTH = 6


class InvalidCredentials(AdminActionError):
    def __init__(self):
        super().__init__("E-mail ou senha incorretos.", status=401)


class AccountUnavailable(AdminActionError):
    """Credencial certa, conta indisponivel. E um caso diferente de senha
    errada, e a pessoa precisa saber qual dos dois aconteceu — dizer
    "senha incorreta" para quem digitou certo manda ela tentar de novo
    para sempre."""

    def __init__(self, message: str):
        super().__init__(message, status=403)


def authenticate(email: str | None, password: str | None) -> dict:
    """Confere e-mail e senha de uma conta de espectador.

    Nunca devolve o hash — so a linha do usuario, ja filtrada pelo
    serializador da API.
    """
    from werkzeug.security import check_password_hash

    email = (email or "").strip().lower()
    password = password or ""

    if not email or not password:
        raise InvalidCredentials()

    user_row = db.get_user_by_email(email)

    # check_password_hash roda mesmo sem usuario para o tempo de resposta
    # nao denunciar quais e-mails existem.
    hash_guardado = (user_row or {}).get("password_hash") or ""
    confere = check_password_hash(hash_guardado, password) if hash_guardado else False

    if not user_row or not confere:
        raise InvalidCredentials()

    if not user_row.get("active"):
        raise AccountUnavailable("Seu acesso está desativado. Fale com o administrador.")

    if not db.is_effectively_active(user_row):
        raise AccountUnavailable("Seu acesso expirou. Fale com o administrador para renovar.")

    return user_row


def user_by_invite(token: str | None) -> dict:
    """Resolve um convite. Convite de conta desativada ou expirada nao
    abre: definir senha ali criaria a expectativa de um acesso que nao
    vai funcionar."""
    token = (token or "").strip()
    if not token:
        raise NotFound("Convite não encontrado.")

    user_row = db.get_user_by_invite_token(token)
    if not user_row:
        raise NotFound("Convite não encontrado.")

    if not db.is_effectively_active(user_row):
        raise AccountUnavailable("Este convite não está mais válido. Fale com o administrador.")

    return user_row


def set_password(uid: str, password: str | None) -> dict:
    from werkzeug.security import generate_password_hash

    user_row = _require_user(uid)
    password = password or ""

    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminActionError(
            f"A senha precisa ter pelo menos {MIN_PASSWORD_LENGTH} caracteres."
        )

    db.set_password(uid, generate_password_hash(password))
    return _require_user(uid)


# --- device and playback state ------------------------------------------

def _minutes_since(moment) -> float | None:
    if moment is None:
        return None
    return (datetime.now(timezone.utc) - moment).total_seconds() / 60


def device_windows() -> tuple[int, int]:
    """As duas janelas, num lugar so: quem lista e quem interpreta usam as
    mesmas, senao a contagem e a leitura discordariam."""
    return (
        int(os.environ.get("DEVICE_SESSION_TTL_MINUTES", "240")),
        int(os.environ.get("PLAYBACK_IDLE_SECONDS", "180")),
    )


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

    device_ttl, playback_secs = device_windows()
    playback_ttl = playback_secs / 60

    playing = playback_idle is not None and playback_idle < playback_ttl
    known = device_idle is not None and device_idle < device_ttl

    # Quantos, nao qual. Enquanto havia limite de um aparelho, a pergunta
    # nao existia. Sem limite, dois tocando ao mesmo tempo e o sinal de que
    # alguem esta usando a conta sem a familia saber - e e o unico jeito de
    # perceber isso, porque ninguem mais e barrado.
    devices = int(user_row.get("device_count") or 0)
    streams = int(user_row.get("playback_count") or 0)

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
        "devices": devices,
        "streams": streams,
        # Duas reproducoes simultaneas nao provam nada sozinhas - a mesma
        # pessoa pode ter comecado na TV e continuado no celular. E o que
        # merece um olhar, e quem decide e quem conhece a familia.
        "concurrent": streams >= 2,
        "last_title": user_row.get("last_title_id"),
        "last_title_at": user_row.get("last_title_at"),
    }


# --- family accounts ----------------------------------------------------

def create_user(
    email: str | None,
    display_name: str | None = None,
    expires_in_days=None,
    drive_account_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Creates a family account, assigning a pool Drive account.

    An explicit choice pins the family to that Drive; leaving it empty
    balances across the pool and stays unpinned, so later rebalancing is
    free to move it.

    `idempotency_key` torna a chamada repetivel: com a mesma chave, a
    segunda tentativa devolve a MESMA conta em vez de erro de e-mail
    duplicado. Existe porque o cadastro escreve em dois bancos que
    nenhuma transacao cobre - falhar depois de criar aqui deixava a pessoa
    sem conta e sem como repetir.
    """
    idempotency_key = _clean_optional(idempotency_key)
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
            idempotency_key=idempotency_key,
        )
    except psycopg.Error as e:
        # Duas chamadas simultaneas com a mesma chave: uma insere, a outra
        # bate no indice unico. Devolver a conta que venceu e a resposta
        # certa - as duas pediam a mesma coisa.
        if idempotency_key:
            try:
                ja = db.user_by_idempotency_key(idempotency_key)
            except Exception:
                ja = None
            if ja:
                return ja

        # Fora isso a causa realista e o UNIQUE do e-mail; o detalhe fica
        # no log e o problema continua sendo de quem chamou.
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
    """Esquece o aparelho e a reproducao registrados.

    Ja destravou acesso: eram duas travas, e este botao era a saida quando
    alguem ficava preso. Nao ha mais trava nenhuma - ninguem e barrado por
    estar em outro aparelho.

    O botao continua util para uma coisa so: zerar o que o painel mostra,
    quando "assistindo agora" fica pendurado numa sessao que ja acabou.
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

def live_drive_status(drive_account_id) -> dict | None:
    """Estado real de uma conta Drive, perguntando ao Google.

    Custa uma chamada de rede por conta, por isso fica fora da listagem:
    quem quiser espaco em disco pede de proposito, e a tela preenche
    depois de ja ter aberto.

    Nunca levanta: uma conta quebrada nao pode derrubar a tela inteira,
    que e justamente onde se conserta a conta quebrada.
    """
    try:
        from sgd.tenancy import _get_drive_instance
        from sgd.routes import drive_status

        gdrive = _get_drive_instance(str(drive_account_id))
        if gdrive is None:
            return None
        return drive_status(gdrive)
    except Exception as e:
        logger.warning("Could not fetch live status for drive %s: %s", drive_account_id, e)
        return None



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
