"""Admin panel.

Two things to manage:
- drive_accounts: the pool of Google Drive accounts you connect yourself,
  all pointing at the same shared folder.
- users: family/viewer accounts, each auto-assigned to whichever pool
  account currently has the fewest people on it, optionally with an
  expiration date.

Auth: a single shared password in the ADMIN_PASSWORD env var. Intentionally
simple - this is for one admin (you), not a multi-admin system.
"""

import functools
import logging
import os
from datetime import datetime, timezone
from html import escape

from flask import abort, redirect, request, session, Response

from sgd import app, db

logger = logging.getLogger(__name__)


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


def _page(body, title="Admin - Stream Helium"):
    html = f"""
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<style>
  :root {{ --bg:#0a0710; --card:rgba(255,255,255,.045); --brd:rgba(255,255,255,.09);
           --txt:#ece8f4; --dim:#a79fbb; --accent:#8b5cf6; --ok:#34d399; --off:#6b7280;
           --warn:#fbbf24; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--txt); padding:2rem 1rem;
          font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
  main {{ max-width:56rem; margin:0 auto; }}
  h1 {{ font-size:1.3rem; }}
  h2 {{ font-size:1.05rem; color:var(--dim); margin-top:2.5rem; }}
  table {{ width:100%; border-collapse:collapse; margin-top:1rem; }}
  th, td {{ text-align:left; padding:.6rem .5rem; border-bottom:1px solid var(--brd); font-size:.9rem; }}
  th {{ color:var(--dim); font-weight:600; }}
  input, select {{ padding:.5rem; border-radius:.4rem; border:1px solid #332844;
           background:#120c1c; color:var(--txt); }}
  button {{ padding:.5rem 1rem; border-radius:.4rem; border:none; cursor:pointer;
            background:var(--accent); color:#fff; font-weight:600; }}
  button.danger {{ background:#ef4444; }}
  button.ghost {{ background:transparent; border:1px solid var(--brd); color:var(--txt); }}
  a {{ color:var(--accent); }}
  .dot {{ display:inline-block; width:.5rem; height:.5rem; border-radius:50%; margin-right:.35rem; }}
  .dot.ok {{ background:var(--ok); }}
  .dot.off {{ background:var(--off); }}
  .dot.warn {{ background:var(--warn); }}
  code {{ background:#120c1c; padding:.15rem .4rem; border-radius:.3rem; font-size:.8rem; }}
  form.inline {{ display:inline; }}
  nav a {{ margin-right:1rem; }}
</style>
<main>
  <nav><a href="/admin">Usuários</a><a href="/admin/drives">Contas Drive</a></nav>
  {body}
</main>
"""
    return Response(html, mimetype="text/html; charset=utf-8")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = ""
    if request.method == "POST":
        if request.form.get("password") == _admin_password():
            session["is_admin"] = True
            return redirect("/admin")
        error = '<p style="color:#ef4444">Senha incorreta.</p>'

    html = f"""
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Login - Admin</title>
<style>
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
          background:#0a0710; color:#ece8f4; font:15px/1.6 system-ui,sans-serif; }}
  input {{ padding:.5rem; border-radius:.4rem; border:1px solid #332844; background:#120c1c; color:#ece8f4; }}
  button {{ padding:.5rem 1rem; border-radius:.4rem; border:none; cursor:pointer; background:#8b5cf6; color:#fff; font-weight:600; }}
</style>
<div>
  <h1>Entrar</h1>
  {error}
  <form method="post">
    <input type="password" name="password" placeholder="Senha do admin" autofocus>
    <button type="submit">Entrar</button>
  </form>
</div>
"""
    return Response(html, mimetype="text/html; charset=utf-8")


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect("/admin/login")


# --- family/viewer accounts -------------------------------------------

@app.route("/admin")
@require_admin
def admin_home():
    users = db.list_users()
    drive_accounts = db.list_drive_accounts()
    connected_drives = [d for d in drive_accounts if d["active"] and d["connected"]]
    drive_options = "\n".join(
        f'<option value="{d["id"]}">{escape(d["label"])}</option>' for d in connected_drives
    )
    rows = "\n".join(_user_row(u, connected_drives) for u in users) or (
        '<tr><td colspan="7" style="color:var(--dim)">Nenhuma conta ainda.</td></tr>'
    )

    if not drive_accounts:
        drive_warning = (
            '<p style="color:var(--warn)">Nenhuma conta Drive no pool ainda - '
            'cadastre uma em <a href="/admin/drives">Contas Drive</a> antes de '
            'criar contas de família, senão elas ficam sem addon funcional.</p>'
        )
    else:
        drive_warning = ""

    return _page(f"""
      <h1>Contas de família</h1>
      {drive_warning}
      <form method="post" action="/admin/users" style="display:flex;gap:.5rem;margin:1rem 0;flex-wrap:wrap">
        <input type="email" name="email" placeholder="email@exemplo.com" required style="flex:1;min-width:12rem">
        <input type="text" name="display_name" placeholder="Nome (opcional)" style="flex:1;min-width:8rem">
        <input type="number" name="expires_in_days" placeholder="Expira em (dias, opcional)" min="1" style="width:11rem">
        <select name="drive_account_id">
          <option value="">Automático (equilibrar)</option>
          {drive_options}
        </select>
        <button type="submit">Adicionar</button>
      </form>
      <table>
        <tr><th>Conta</th><th>Drive atribuído</th><th>TMDB</th><th>Senha</th><th>Expira</th><th>Convite</th><th></th></tr>
        {rows}
      </table>
      <form method="post" action="/admin/logout" style="margin-top:2rem">
        <button type="submit" class="ghost">Sair</button>
      </form>
    """)


def _format_expiry(expires_at):
    if not expires_at:
        return '<span style="color:var(--dim)">sem prazo</span>', False
    expired = expires_at <= datetime.now(timezone.utc)
    label = expires_at.strftime("%d/%m/%Y")
    if expired:
        return f'<span style="color:#ef4444">expirou em {label}</span>', True
    return label, False


def _user_row(u, connected_drives):
    is_active = db.is_effectively_active(u)
    status_dot = "ok" if is_active else "off"
    expiry_html, is_expired = _format_expiry(u.get("expires_at"))
    if not u["active"]:
        status_label = "desativado"
    elif is_expired:
        status_label = "expirado"
        status_dot = "warn"
    else:
        status_label = "ativo"

    tmdb = "sim" if u["tmdb_connected"] else "—"
    senha = "definida" if u.get("has_password") else "—"
    invite_url = f"/connect/{u['invite_token']}"
    name = escape(u["display_name"] or "")
    toggle_label = "Desativar" if u["active"] else "Ativar"
    pin_note = ' 📌' if u.get("drive_account_pinned") else ""
    drive_label = escape(u.get("drive_label") or "—") + pin_note

    current_drive_id = str(u.get("drive_account_id") or "")
    reassign_options = "\n".join(
        f'<option value="{d["id"]}"{" selected" if str(d["id"]) == current_drive_id else ""}>'
        f'{escape(d["label"])}</option>'
        for d in connected_drives
    )
    auto_button = (
        f'<form class="inline" method="post" action="/admin/users/{u["id"]}/auto-assign">'
        f'<button type="submit" class="ghost" title="Volta a ser rebalanceado automaticamente">Auto</button>'
        f'</form>'
        if u.get("drive_account_pinned") else ""
    )

    return f"""
    <tr>
      <td><span class="dot {status_dot}"></span>{escape(u['email'])}
          {f'<br><span style="color:var(--dim);font-size:.8rem">{name}</span>' if name else ''}
          <br><span style="color:var(--dim);font-size:.75rem">{status_label}</span></td>
      <td>{drive_label}
        <form class="inline" method="post" action="/admin/users/{u['id']}/reassign" style="display:block;margin-top:.3rem">
          <select name="drive_account_id" style="font-size:.8rem;padding:.2rem">
            {reassign_options}
          </select>
          <button type="submit" class="ghost" style="font-size:.8rem;padding:.2rem .5rem" title="Fixa nessa conta">Fixar</button>
        </form>
        {auto_button}
      </td>
      <td>{tmdb}</td>
      <td>{senha}
        {'<form class="inline" method="post" action="/admin/users/' + str(u['id']) + '/reset-password" onsubmit="return confirm(\'Resetar a senha? A pessoa precisa definir uma nova pelo link de convite.\')"><button type="submit" class="ghost" style="font-size:.75rem;padding:.2rem .4rem">Resetar</button></form>' if u.get('has_password') else ''}
      </td>
      <td>{expiry_html}</td>
      <td><a href="{invite_url}" target="_blank"><code>{invite_url}</code></a></td>
      <td style="white-space:nowrap">
        <form class="inline" method="post" action="/admin/users/{u['id']}/renew">
          <input type="hidden" name="days" value="30">
          <button type="submit" class="ghost" title="Renovar por mais 30 dias">+30d</button>
        </form>
        <form class="inline" method="post" action="/admin/users/{u['id']}/toggle">
          <button type="submit" class="ghost">{toggle_label}</button>
        </form>
        <form class="inline" method="post" action="/admin/users/{u['id']}/delete"
              onsubmit="return confirm('Remover {escape(u['email'])}? A URL do addon dele para de funcionar imediatamente.')">
          <button type="submit" class="danger">Remover</button>
        </form>
      </td>
    </tr>
    """


@app.route("/admin/users", methods=["POST"])
@require_admin
def admin_create_user():
    email = (request.form.get("email") or "").strip().lower()
    display_name = (request.form.get("display_name") or "").strip() or None
    expires_raw = (request.form.get("expires_in_days") or "").strip()
    expires_in_days = int(expires_raw) if expires_raw.isdigit() else None
    chosen_drive_id = (request.form.get("drive_account_id") or "").strip() or None
    if not email or "@" not in email:
        abort(400)

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
    except Exception as e:
        logger.warning("Failed to create user %s: %s", email, e)
    return redirect("/admin")


@app.route("/admin/users/<uid>/toggle", methods=["POST"])
@require_admin
def admin_toggle_user(uid):
    user_row = db.get_user(uid)
    if not user_row:
        abort(404)
    db.set_active(uid, not user_row["active"])
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
    return redirect("/admin")


@app.route("/admin/users/<uid>/reassign", methods=["POST"])
@require_admin
def admin_reassign_user(uid):
    """Pins this family account to a specific pool account. Once pinned,
    the account won't move even if you later delete/add other pool
    accounts - i.e. everything except deleting THIS SPECIFIC pool account,
    which leaves nothing to pin to."""
    from sgd.tenancy import _drive_cache
    user_row = db.get_user(uid)
    if not user_row:
        abort(404)
    drive_account_id = (request.form.get("drive_account_id") or "").strip() or None
    if not drive_account_id:
        abort(400)
    db.reassign_drive_account(uid, drive_account_id, pinned=True)
    _drive_cache.pop(str(drive_account_id), None)
    return redirect("/admin")


@app.route("/admin/users/<uid>/auto-assign", methods=["POST"])
@require_admin
def admin_auto_assign_user(uid):
    """Un-pins this family account and immediately rebalances it to
    whichever pool account currently has the fewest people."""
    user_row = db.get_user(uid)
    if not user_row:
        abort(404)
    auto = db.pick_least_loaded_drive_account()
    db.reassign_drive_account(uid, str(auto["id"]) if auto else None, pinned=False)
    return redirect("/admin")


@app.route("/admin/users/<uid>/reset-password", methods=["POST"])
@require_admin
def admin_reset_password(uid):
    user_row = db.get_user(uid)
    if not user_row:
        abort(404)
    db.clear_password(uid)
    return redirect("/admin")


@app.route("/admin/users/<uid>/delete", methods=["POST"])
@require_admin
def admin_delete_user(uid):
    db.delete_user(uid)
    return redirect("/admin")


# --- drive account pool -------------------------------------------------

@app.route("/admin/drives")
@require_admin
def admin_drives():
    drive_accounts = db.list_drive_accounts()
    rows = "\n".join(_drive_row(d) for d in drive_accounts) or (
        '<tr><td colspan="4" style="color:var(--dim)">Nenhuma conta Drive ainda.</td></tr>'
    )

    return _page(f"""
      <h1>Contas Drive (pool)</h1>
      <p style="color:var(--dim)">Todas devem apontar pra mesma pasta compartilhada.
      Novas contas de família são atribuídas automaticamente a quem tiver menos gente.</p>
      <form method="post" action="/admin/drives" style="display:flex;gap:.5rem;margin:1rem 0">
        <input type="text" name="label" placeholder="Nome (ex: Drive 1)" required style="flex:1">
        <button type="submit">Adicionar</button>
      </form>
      <table>
        <tr><th>Conta</th><th>Status</th><th>Famílias atribuídas</th><th></th></tr>
        {rows}
      </table>
    """, title="Contas Drive - Admin")


def _drive_row(d):
    if not d["connected"]:
        status = '<span class="dot warn"></span>não conectado'
    elif not d["active"]:
        status = '<span class="dot off"></span>desativado'
    else:
        status = '<span class="dot ok"></span>conectado'

    connect_label = "Reconectar" if d["connected"] else "Conectar ao Google"
    toggle_label = "Desativar" if d["active"] else "Ativar"

    return f"""
    <tr>
      <td>{escape(d['label'])}</td>
      <td>{status}</td>
      <td>{d['assigned_count']}</td>
      <td style="white-space:nowrap">
        <a href="/admin/drives/{d['id']}/connect-google" style="display:inline-block;padding:.5rem 1rem;
           border-radius:.4rem;background:var(--accent);color:#fff;text-decoration:none;margin-right:.3rem">{connect_label}</a>
        <form class="inline" method="post" action="/admin/drives/{d['id']}/toggle">
          <button type="submit" class="ghost">{toggle_label}</button>
        </form>
        <form class="inline" method="post" action="/admin/drives/{d['id']}/delete"
              onsubmit="return confirm('Remover {escape(d['label'])}? As {d['assigned_count']} famílias nela serão redistribuídas automaticamente entre as demais contas do pool.')">
          <button type="submit" class="danger">Remover</button>
        </form>
      </td>
    </tr>
    """


@app.route("/admin/drives", methods=["POST"])
@require_admin
def admin_create_drive():
    label = (request.form.get("label") or "").strip()
    if not label:
        abort(400)
    db.create_drive_account(label)
    return redirect("/admin/drives")


@app.route("/admin/drives/<did>/toggle", methods=["POST"])
@require_admin
def admin_toggle_drive(did):
    drive_row = db.get_drive_account(did)
    if not drive_row:
        abort(404)
    db.set_drive_account_active(did, not drive_row["active"])
    return redirect("/admin/drives")


@app.route("/admin/drives/<did>/delete", methods=["POST"])
@require_admin
def admin_delete_drive(did):
    from sgd.tenancy import _drive_cache
    db.redistribute_and_delete_drive_account(did)
    _drive_cache.pop(str(did), None)
    return redirect("/admin/drives")
