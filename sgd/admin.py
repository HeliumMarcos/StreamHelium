"""Admin panel.

Three pools to manage, all admin-only:
- drive_accounts: Google Drive accounts, all pointing at the same shared
  folder.
- tmdb_keys: TMDB API keys, used round-robin to spread rate-limit load.
- users: family/viewer accounts, each auto-assigned to whichever pool
  account currently has the fewest people, optionally with an expiration
  date and a one-device-at-a-time limit.

Auth: a single shared password in the ADMIN_PASSWORD env var. Intentionally
simple - this is for one admin (you), not a multi-admin system.
"""

import functools
import hmac
import logging
import os
import secrets
from datetime import datetime, timezone

from flask import abort, flash, get_flashed_messages, redirect, render_template, request, session

from sgd import app, db

logger = logging.getLogger(__name__)


def _live_drive_status(drive_account_id):
    """Best-effort - never raises, since one broken account shouldn't take
    down the whole admin page."""
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


def _admin_password():
    pw = os.environ.get("ADMIN_PASSWORD")
    if not pw:
        raise RuntimeError("ADMIN_PASSWORD environment variable is not set")
    return pw


def require_admin(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect("/admin/login")
        return view(*args, **kwargs)
    return wrapped


# --- shared chrome ----------------------------------------------------

def _csrf_token():
    token = session.get("admin_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["admin_csrf_token"] = token
    return token


@app.before_request
def protect_admin_csrf():
    """Require a session-bound token for every administrative mutation."""
    if request.method != "POST" or not request.path.startswith("/admin"):
        return None
    if request.path != "/admin/login" and not session.get("is_admin"):
        return None
    expected = session.get("admin_csrf_token", "")
    supplied = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return render_template(
            "admin/csrf_error.html",
            is_admin=bool(session.get("is_admin")),
        ), 400
    return None


def _page(template, title="Admin - Stream Helium", active="", **context):
    nav_items = [
        {"href": "/admin", "label": "Famílias", "key": "users"},
        {"href": "/admin/drives", "label": "Contas Drive", "key": "drives"},
        {"href": "/admin/tmdb", "label": "Chaves TMDB", "key": "tmdb"},
    ]
    return render_template(
        template,
        title=title,
        active=active,
        nav_items=nav_items,
        alerts=get_flashed_messages(with_categories=True),
        csrf_token=_csrf_token(),
        **context,
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == _admin_password():
            session.clear()
            session["is_admin"] = True
            _csrf_token()
            return redirect("/admin")
        error = "Senha incorreta."

    return render_template("admin/login.html", error=error, csrf_token=_csrf_token())


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect("/admin/login")


def _expiry_view(expires_at):
    if not expires_at:
        return {"label": "sem prazo", "class_name": "muted", "expired": False}
    expired = expires_at <= datetime.now(timezone.utc)
    date_label = expires_at.strftime("%d/%m/%Y")
    if expired:
        return {"label": f"expirou {date_label}", "class_name": "error-text", "expired": True}
    days_left = (expires_at - datetime.now(timezone.utc)).days
    return {
        "label": f"{date_label} ({days_left}d)",
        "class_name": "warning-text" if days_left <= 3 else "",
        "expired": False,
    }


def _device_view(user_id):
    try:
        row = db.get_device_session(user_id)
    except Exception:
        row = None
    if not row:
        return {"active": False, "dot": "", "label": "nenhum", "seen": ""}

    age_minutes = (datetime.now(timezone.utc) - row["last_seen"]).total_seconds() / 60
    ttl = int(os.environ.get("DEVICE_SESSION_TTL_MINUTES", "240"))
    if age_minutes >= ttl:
        return {"active": False, "dot": "", "label": "livre (inativo)", "seen": ""}
    return {
        "active": True,
        "dot": "ok",
        "label": (row.get("device_label") or "dispositivo")[:40],
        "seen": "agora" if age_minutes < 1 else f"há {int(age_minutes)}min",
    }


def _user_view(row):
    expiry = _expiry_view(row.get("expires_at"))
    effectively_active = db.is_effectively_active(row)
    status_dot = "ok" if effectively_active else ""
    if not row["active"]:
        status_label = "desativado"
    elif expiry["expired"]:
        status_label, status_dot = "expirado", "warn"
    else:
        status_label = "ativo"
    return {
        **row,
        "display_name": row.get("display_name") or "",
        "drive_label": row.get("drive_label") or "—",
        "drive_account_pinned": bool(row.get("drive_account_pinned")),
        "current_drive_id": str(row.get("drive_account_id") or ""),
        "has_password": bool(row.get("has_password")),
        "expires_value": row["expires_at"].strftime("%Y-%m-%d") if row.get("expires_at") else "",
        "expiry": expiry,
        "status_dot": status_dot,
        "status_label": status_label,
        "toggle_label": "Desativar" if row["active"] else "Ativar",
        "invite_url": f"/connect/{row['invite_token']}",
        "device": _device_view(str(row["id"])),
    }


# --- family/viewer accounts -------------------------------------------

@app.route("/admin")
@require_admin
def admin_home():
    drive_accounts = db.list_drive_accounts()
    connected_drives = [d for d in drive_accounts if d["active"] and d["connected"]]
    return _page(
        "admin/users.html",
        active="users",
        users=[_user_view(row) for row in db.list_users()],
        drive_accounts=drive_accounts,
        connected_drives=connected_drives,
        overview=db.get_admin_overview(),
    )


@app.route("/admin/users", methods=["POST"])
@require_admin
def admin_create_user():
    email = (request.form.get("email") or "").strip().lower()
    display_name = (request.form.get("display_name") or "").strip() or None
    expires_raw = (request.form.get("expires_in_days") or "").strip()
    expires_in_days = int(expires_raw) if expires_raw.isdigit() else None
    chosen_drive_id = (request.form.get("drive_account_id") or "").strip() or None
    if not email or "@" not in email:
        flash("Informe um endereço de e-mail válido.", "error")
        return redirect("/admin")

    try:
        if chosen_drive_id:
            drive_account_id, pinned = chosen_drive_id, True
        else:
            auto = db.pick_least_loaded_drive_account()
            drive_account_id = str(auto["id"]) if auto else None
            pinned = False
        db.create_user(
            email, display_name,
            drive_account_id=drive_account_id,
            expires_in_days=expires_in_days,
            pinned=pinned,
        )
        flash("Conta de família criada com sucesso.", "success")
    except Exception as e:
        logger.warning("Failed to create user %s: %s", email, e)
        flash("Não foi possível criar a conta. Verifique se o e-mail já está cadastrado.", "error")
    return redirect("/admin")


@app.route("/admin/users/<uid>/toggle", methods=["POST"])
@require_admin
def admin_toggle_user(uid):
    user_row = db.get_user(uid)
    if not user_row:
        abort(404)
    db.set_active(uid, not user_row["active"])
    flash("Status da conta atualizado.", "success")
    return redirect("/admin")


@app.route("/admin/users/<uid>/edit", methods=["POST"])
@require_admin
def admin_edit_user(uid):
    user_row = db.get_user(uid)
    if not user_row:
        abort(404)

    email = (request.form.get("email") or "").strip().lower()
    display_name = (request.form.get("display_name") or "").strip() or None
    clear_expiration = request.form.get("no_expiration") == "on"
    expires_raw = (request.form.get("expires_at") or "").strip()

    if not email or "@" not in email:
        flash("Informe um endereço de e-mail válido.", "error")
        return redirect("/admin")

    expires_at = None
    if not clear_expiration and expires_raw:
        try:
            expires_at = datetime.strptime(expires_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            flash("Informe uma data de expiração válida.", "error")
            return redirect("/admin")

    db.update_user(uid, email, display_name, expires_at, clear_expiration=clear_expiration)
    flash("Dados da conta atualizados.", "success")
    return redirect("/admin")


@app.route("/admin/users/<uid>/renew", methods=["POST"])
@require_admin
def admin_renew_user(uid):
    user_row = db.get_user(uid)
    if not user_row:
        abort(404)
    days_raw = (request.form.get("days") or "30").strip()
    days = int(days_raw) if days_raw.isdigit() else 30
    db.renew_user(uid, days)
    flash(f"Acesso renovado por {days} dias.", "success")
    return redirect("/admin")


@app.route("/admin/users/<uid>/reassign", methods=["POST"])
@require_admin
def admin_reassign_user(uid):
    """Pins this family account to a specific pool account."""
    from sgd.tenancy import _drive_cache
    user_row = db.get_user(uid)
    if not user_row:
        abort(404)
    drive_account_id = (request.form.get("drive_account_id") or "").strip() or None
    if not drive_account_id:
        flash("Escolha uma conta Drive para fazer a atribuição.", "error")
        return redirect("/admin")
    db.reassign_drive_account(uid, drive_account_id, pinned=True)
    _drive_cache.pop(str(drive_account_id), None)
    flash("Conta Drive fixada para esta família.", "success")
    return redirect("/admin")


@app.route("/admin/users/<uid>/auto-assign", methods=["POST"])
@require_admin
def admin_auto_assign_user(uid):
    user_row = db.get_user(uid)
    if not user_row:
        abort(404)
    auto = db.pick_least_loaded_drive_account()
    db.reassign_drive_account(uid, str(auto["id"]) if auto else None, pinned=False)
    flash("Distribuição automática reativada.", "success")
    return redirect("/admin")


@app.route("/admin/users/<uid>/reset-password", methods=["POST"])
@require_admin
def admin_reset_password(uid):
    user_row = db.get_user(uid)
    if not user_row:
        abort(404)
    db.clear_password(uid)
    flash("Senha removida. A pessoa deverá criar uma nova pelo convite.", "success")
    return redirect("/admin")


@app.route("/admin/users/<uid>/free-device", methods=["POST"])
@require_admin
def admin_free_device(uid):
    db.clear_device_session(uid)
    flash("Dispositivo liberado.", "success")
    return redirect("/admin")


@app.route("/admin/users/<uid>/delete", methods=["POST"])
@require_admin
def admin_delete_user(uid):
    db.delete_user(uid)
    flash("Conta de família removida.", "success")
    return redirect("/admin")


# --- drive account pool -------------------------------------------------

@app.route("/admin/drives")
@require_admin
def admin_drives():
    drive_accounts = db.list_drive_accounts()
    cf_configured = bool(os.environ.get("CF_PROXY_URL"))
    proxy_on = db.get_setting("cf_proxy_enabled", default="1") != "0"
    return _page(
        "admin/drives.html",
        title="Contas Drive - Admin",
        active="drives",
        drives=[_drive_view(row) for row in drive_accounts],
        cf_configured=cf_configured,
        proxy_on=proxy_on,
    )


@app.route("/admin/settings/proxy/toggle", methods=["POST"])
@require_admin
def admin_toggle_proxy():
    current = db.get_setting("cf_proxy_enabled", default="1") != "0"
    db.set_setting("cf_proxy_enabled", "0" if current else "1")
    flash(f"Proxy Cloudflare {'desativado' if current else 'ativado'}.", "success")
    return redirect("/admin/drives")


def _drive_view(row):
    view = {
        **row,
        "connect_label": "Reconectar" if row["connected"] else "Conectar ao Google",
        "toggle_label": "Desativar" if row["active"] else "Ativar",
        "account": "",
        "usage_label": "",
        "usage_pct": None,
        "error": "",
    }
    if not row["connected"]:
        view.update(status_dot="warn", status_label="não conectado")
        return view
    if not row["active"]:
        view.update(status_dot="", status_label="desativado")
        return view

    view.update(status_dot="ok", status_label="conectado")
    live = _live_drive_status(row["id"])
    if live and live.get("connected"):
        usage = live.get("usage_human", "?")
        limit = live.get("limit_human")
        try:
            usage_pct = max(0.0, min(100.0, float(live["usage_pct"])))
        except (KeyError, TypeError, ValueError):
            usage_pct = None
        view.update(
            account=live.get("account") or "",
            usage_label=f"{usage}{' de ' + limit if limit else ''} em uso",
            usage_pct=usage_pct,
        )
    elif live:
        reconnect_required = bool(live.get("reconnect_required"))
        view.update(
            status_dot="warn",
            status_label=(
                "reconexão necessária"
                if reconnect_required
                else "temporariamente indisponível"
            ),
            error=live.get("error", ""),
        )
    return view


@app.route("/admin/drives/worker-config")
@require_admin
def admin_drives_worker_config():
    import json as _json
    from sgd.crypto import decrypt

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    drive_accounts = db.list_drive_accounts()

    config = {}
    for d in drive_accounts:
        if not d["connected"]:
            continue
        full_row = db.get_drive_account(str(d["id"]))
        refresh_token = decrypt(full_row.get("google_refresh_token"))
        config[str(d["id"])] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }

    shared_secret_set = bool(os.environ.get("PROXY_SHARED_SECRET"))
    return _page(
        "admin/worker_config.html",
        title="Config do Worker - Admin",
        active="drives",
        shared_secret_set=shared_secret_set,
        token_endpoint=f"https://{request.host}/internal/drive-token",
        config_json=_json.dumps(config, indent=2, ensure_ascii=False),
    )


@app.route("/admin/drives", methods=["POST"])
@require_admin
def admin_create_drive():
    label = (request.form.get("label") or "").strip()
    if not label:
        flash("Informe um nome para a conta Drive.", "error")
        return redirect("/admin/drives")
    db.create_drive_account(label)
    flash("Conta Drive adicionada. Agora conecte-a ao Google.", "success")
    return redirect("/admin/drives")


@app.route("/admin/drives/<did>/toggle", methods=["POST"])
@require_admin
def admin_toggle_drive(did):
    drive_row = db.get_drive_account(did)
    if not drive_row:
        abort(404)
    db.set_drive_account_active(did, not drive_row["active"])
    flash("Status da conta Drive atualizado.", "success")
    return redirect("/admin/drives")


@app.route("/admin/drives/<did>/delete", methods=["POST"])
@require_admin
def admin_delete_drive(did):
    from sgd.tenancy import _drive_cache
    affected = db.redistribute_and_delete_drive_account(did)
    _drive_cache.pop(str(did), None)
    flash(f"Conta Drive removida; {affected} família(s) redistribuída(s).", "success")
    return redirect("/admin/drives")


# --- TMDB key pool --------------------------------------------------------

def _mask_key(plain_or_none):
    if not plain_or_none:
        return "—"
    if len(plain_or_none) <= 4:
        return "•" * len(plain_or_none)
    return "•" * (len(plain_or_none) - 4) + plain_or_none[-4:]


@app.route("/admin/tmdb")
@require_admin
def admin_tmdb():
    from sgd.crypto import decrypt
    keys = db.list_tmdb_keys()
    return _page(
        "admin/tmdb.html",
        title="Chaves TMDB - Admin",
        active="tmdb",
        keys=[{**row, "masked_key": _mask_key(decrypt(row["api_key"]))} for row in keys],
    )


@app.route("/admin/tmdb", methods=["POST"])
@require_admin
def admin_create_tmdb():
    label = (request.form.get("label") or "").strip()
    api_key = (request.form.get("api_key") or "").strip()
    if not label or not api_key:
        flash("Informe o nome e a chave da API TMDB.", "error")
        return redirect("/admin/tmdb")
    db.create_tmdb_key(label, api_key)
    flash("Chave TMDB adicionada.", "success")
    return redirect("/admin/tmdb")


@app.route("/admin/tmdb/<tid>/toggle", methods=["POST"])
@require_admin
def admin_toggle_tmdb(tid):
    key_row = db.get_tmdb_key(tid)
    if not key_row:
        abort(404)
    db.set_tmdb_key_active(tid, not key_row["active"])
    flash("Status da chave TMDB atualizado.", "success")
    return redirect("/admin/tmdb")


@app.route("/admin/tmdb/<tid>/delete", methods=["POST"])
@require_admin
def admin_delete_tmdb(tid):
    db.delete_tmdb_key(tid)
    flash("Chave TMDB removida.", "success")
    return redirect("/admin/tmdb")
