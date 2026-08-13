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
import logging
import os
from datetime import datetime, timezone
from html import escape

from flask import abort, redirect, request, session, Response

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

def _page(body, title="Admin - Stream Helium", active=""):
    def nav_item(href, label, key):
        cls = "nav-item active" if key == active else "nav-item"
        return f'<a class="{cls}" href="{href}">{label}</a>'

    nav = "".join([
        nav_item("/admin", "Famílias", "users"),
        nav_item("/admin/drives", "Contas Drive", "drives"),
        nav_item("/admin/tmdb", "Chaves TMDB", "tmdb"),
    ])

    html = f"""
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<style>
  :root {{
    --bg:#0a0710; --bg-soft:#120c1c; --card:rgba(255,255,255,.045);
    --card-hover:rgba(255,255,255,.07); --brd:rgba(255,255,255,.09);
    --txt:#ece8f4; --dim:#a79fbb; --accent:#8b5cf6; --accent-2:#22d3ee;
    --ok:#34d399; --off:#6b7280; --warn:#fbbf24; --err:#ef4444;
  }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; background:
      radial-gradient(60% 45% at 15% -5%, rgba(139,92,246,.20), transparent 65%),
      radial-gradient(50% 40% at 100% 0%, rgba(34,211,238,.12), transparent 60%),
      var(--bg);
    color:var(--txt); min-height:100vh;
    font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  }}
  a {{ color:var(--accent-2); text-decoration:none; }}
  main {{ max-width:64rem; margin:0 auto; padding:1.75rem 1.25rem 4rem; }}

  .topbar {{ display:flex; align-items:center; justify-content:space-between;
             flex-wrap:wrap; gap:.75rem; margin-bottom:1.5rem; }}
  .brand {{ font-weight:700; font-size:1.05rem; letter-spacing:.01em; }}
  .brand span {{ color:var(--accent-2); }}
  nav {{ display:flex; gap:.35rem; background:var(--card); border:1px solid var(--brd);
         padding:.3rem; border-radius:.7rem; }}
  .nav-item {{ padding:.4rem .85rem; border-radius:.5rem; color:var(--dim);
               font-size:.88rem; font-weight:600; }}
  .nav-item.active {{ background:var(--accent); color:#fff; }}
  .nav-item:hover:not(.active) {{ background:var(--card-hover); color:var(--txt); }}

  h1 {{ font-size:1.35rem; margin:.2rem 0 .3rem; }}
  h2 {{ font-size:1rem; color:var(--dim); font-weight:600; margin:2.2rem 0 .8rem; }}
  .lede {{ color:var(--dim); font-size:.92rem; margin:0 0 1.2rem; max-width:38rem; }}

  .stats {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(9.5rem, 1fr));
            gap:.7rem; margin-bottom:1.6rem; }}
  .stat {{ background:var(--card); border:1px solid var(--brd); border-radius:.8rem;
           padding:.9rem 1rem; }}
  .stat .n {{ font-size:1.5rem; font-weight:700; line-height:1.1; }}
  .stat .l {{ color:var(--dim); font-size:.78rem; margin-top:.2rem; }}
  .stat.warn .n {{ color:var(--warn); }}
  .stat.ok .n {{ color:var(--ok); }}

  .panel {{ background:var(--card); border:1px solid var(--brd); border-radius:.9rem;
            padding:1.2rem; margin-bottom:1.2rem; }}

  table {{ width:100%; border-collapse:collapse; }}
  th, td {{ text-align:left; padding:.65rem .55rem; border-bottom:1px solid var(--brd);
            font-size:.86rem; vertical-align:top; }}
  th {{ color:var(--dim); font-weight:600; font-size:.78rem; text-transform:uppercase;
        letter-spacing:.03em; }}
  tr:last-child td {{ border-bottom:none; }}

  input, select {{ padding:.5rem .6rem; border-radius:.5rem; border:1px solid #332844;
           background:var(--bg-soft); color:var(--txt); font-size:.88rem; }}
  input::placeholder {{ color:#5f5670; }}
  button {{ padding:.5rem .95rem; border-radius:.5rem; border:none; cursor:pointer;
            background:var(--accent); color:#fff; font-weight:600; font-size:.85rem;
            transition:filter .12s; }}
  button:hover {{ filter:brightness(1.12); }}
  button.danger {{ background:var(--err); }}
  button.ghost {{ background:transparent; border:1px solid var(--brd); color:var(--txt); }}
  button.ghost:hover {{ background:var(--card-hover); }}
  button.sm {{ font-size:.75rem; padding:.25rem .55rem; }}
  .btn-link {{ display:inline-block; padding:.5rem .95rem; border-radius:.5rem;
               background:var(--accent); color:#fff; font-weight:600; font-size:.85rem; }}

  .dot {{ display:inline-block; width:.5rem; height:.5rem; border-radius:50%;
          margin-right:.4rem; background:var(--off); }}
  .dot.ok {{ background:var(--ok); box-shadow:0 0 6px rgba(52,211,153,.6); }}
  .dot.warn {{ background:var(--warn); }}
  .dot.err {{ background:var(--err); }}
  .badge {{ display:inline-block; font-size:.72rem; padding:.1rem .45rem; border-radius:1rem;
            background:var(--card-hover); color:var(--dim); margin-left:.3rem; }}

  code {{ background:var(--bg-soft); padding:.15rem .4rem; border-radius:.35rem;
          font-size:.78rem; }}
  form.inline {{ display:inline; }}
  .row-actions {{ display:flex; flex-wrap:wrap; gap:.3rem; }}
  .muted {{ color:var(--dim); }}
  .name {{ color:var(--dim); font-size:.8rem; }}
  .status-label {{ font-size:.76rem; color:var(--dim); margin-top:.15rem; }}

  form.create {{ display:flex; gap:.55rem; flex-wrap:wrap; align-items:center;
                 margin-bottom:.3rem; }}
  form.create input, form.create select {{ flex:1; min-width:9rem; }}

  footer.admin-footer {{ margin-top:2.5rem; }}

  @media (max-width:640px) {{
    table, thead, tbody, tr {{ display:block; }}
    th {{ display:none; }}
    tr {{ display:block; border:1px solid var(--brd); border-radius:.7rem; padding:.7rem;
          margin-bottom:.7rem; }}
    td {{ display:block; width:100%; border:none; padding:.35rem 0; }}
    td::before {{ content: attr(data-label); display:block; color:var(--dim); font-size:.7rem;
                  text-transform:uppercase; letter-spacing:.03em; margin-bottom:.15rem; }}
    td select {{ width:100%; }}
    .row-actions {{ justify-content:flex-start; }}
    form.create {{ flex-direction:column; align-items:stretch; }}
    form.create input, form.create select {{ width:100%; }}
    .stats {{ grid-template-columns:repeat(2, 1fr); }}
  }}
</style>
<main>
  <div class="topbar">
    <div class="brand">🎬 Stream Helium <span>· admin</span></div>
    <nav>{nav}</nav>
  </div>
  {body}
  <footer class="admin-footer">
    <form method="post" action="/admin/logout">
      <button type="submit" class="ghost">Sair</button>
    </form>
  </footer>
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
          background:radial-gradient(60% 50% at 50% 0%, rgba(139,92,246,.22), transparent 65%) #0a0710;
          color:#ece8f4; font:15px/1.6 system-ui,sans-serif; }}
  .box {{ text-align:center; }}
  h1 {{ font-size:1.2rem; }}
  input {{ padding:.6rem .7rem; border-radius:.5rem; border:1px solid #332844;
           background:#120c1c; color:#ece8f4; width:16rem; }}
  button {{ padding:.6rem 1.1rem; border-radius:.5rem; border:none; cursor:pointer;
            background:#8b5cf6; color:#fff; font-weight:600; margin-left:.4rem; }}
</style>
<div class="box">
  <h1>🎬 Stream Helium · admin</h1>
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


def _stat_cards(overview):
    fam, drv, tmdb, dev = overview["family"], overview["drives"], overview["tmdb"], overview["devices"]
    return f"""
    <div class="stats">
      <div class="stat"><div class="n">{fam['total']}</div><div class="l">Contas de família</div></div>
      <div class="stat ok"><div class="n">{fam['active_count']}</div><div class="l">Ativas</div></div>
      <div class="stat warn"><div class="n">{fam['expired_count']}</div><div class="l">Expiradas</div></div>
      <div class="stat"><div class="n">{drv['connected']}/{drv['total']}</div><div class="l">Drives conectados</div></div>
      <div class="stat"><div class="n">{tmdb['active_count']}/{tmdb['total']}</div><div class="l">Chaves TMDB ativas</div></div>
      <div class="stat"><div class="n">{dev['total']}</div><div class="l">Dispositivos ativos (4h)</div></div>
    </div>
    """


# --- family/viewer accounts -------------------------------------------

@app.route("/admin")
@require_admin
def admin_home():
    users = db.list_users()
    drive_accounts = db.list_drive_accounts()
    overview = db.get_admin_overview()
    connected_drives = [d for d in drive_accounts if d["active"] and d["connected"]]
    drive_options = "\n".join(
        f'<option value="{d["id"]}">{escape(d["label"])}</option>' for d in connected_drives
    )
    rows = "\n".join(_user_row(u, connected_drives) for u in users) or (
        '<tr><td colspan="6" class="muted">Nenhuma conta ainda.</td></tr>'
    )

    if not drive_accounts:
        drive_warning = (
            '<p class="lede" style="color:var(--warn)">⚠️ Nenhuma conta Drive no pool ainda - '
            'cadastre uma em <a href="/admin/drives">Contas Drive</a> antes de '
            'criar contas de família.</p>'
        )
    else:
        drive_warning = ""

    return _page(f"""
      <h1>Painel</h1>
      <p class="lede">Visão geral do sistema e gestão das contas de família.</p>
      {_stat_cards(overview)}

      <h2>Contas de família</h2>
      {drive_warning}
      <div class="panel">
        <form class="create" method="post" action="/admin/users">
          <input type="email" name="email" placeholder="email@exemplo.com" required>
          <input type="text" name="display_name" placeholder="Nome (opcional)">
          <input type="number" name="expires_in_days" placeholder="Expira em (dias)" min="1">
          <select name="drive_account_id">
            <option value="">Automático (equilibrar)</option>
            {drive_options}
          </select>
          <button type="submit">Adicionar</button>
        </form>
      </div>
      <div class="panel">
        <table>
          <tr><th>Conta</th><th>Drive</th><th>Dispositivo</th><th>Senha</th><th>Expira</th><th>Convite / Ações</th></tr>
          {rows}
        </table>
      </div>
    """, active="users")


def _format_expiry(expires_at):
    if not expires_at:
        return '<span class="muted">sem prazo</span>', False
    expired = expires_at <= datetime.now(timezone.utc)
    label = expires_at.strftime("%d/%m/%Y")
    if expired:
        return f'<span style="color:var(--err)">expirou {label}</span>', True
    days_left = (expires_at - datetime.now(timezone.utc)).days
    color = "var(--warn)" if days_left <= 3 else "var(--txt)"
    return f'<span style="color:{color}">{label} <span class="muted">({days_left}d)</span></span>', False


def _format_device(user_id):
    try:
        session_row = db.get_device_session(user_id)
    except Exception:
        session_row = None
    if not session_row:
        return '<span class="dot"></span><span class="muted">nenhum</span>'

    age_minutes = (datetime.now(timezone.utc) - session_row["last_seen"]).total_seconds() / 60
    ttl = int(os.environ.get("DEVICE_SESSION_TTL_MINUTES", "240"))
    if age_minutes >= ttl:
        return '<span class="dot"></span><span class="muted">livre (inativo)</span>'

    label = escape((session_row.get("device_label") or "dispositivo")[:40])
    seen = "agora" if age_minutes < 1 else f"há {int(age_minutes)}min"
    return (
        f'<span class="dot ok"></span>{label}<br>'
        f'<span class="status-label">visto {seen}</span><br>'
        f'<form class="inline" method="post" action="/admin/users/{user_id}/free-device" style="margin-top:.2rem">'
        f'<button type="submit" class="ghost sm">Liberar</button></form>'
    )


def _user_row(u, connected_drives):
    is_active = db.is_effectively_active(u)
    status_dot = "ok" if is_active else ""
    expiry_html, is_expired = _format_expiry(u.get("expires_at"))
    if not u["active"]:
        status_label = "desativado"
    elif is_expired:
        status_label = "expirado"
        status_dot = "warn"
    else:
        status_label = "ativo"

    senha = "definida" if u.get("has_password") else "—"
    invite_url = f"/connect/{u['invite_token']}"
    name = escape(u["display_name"] or "")
    toggle_label = "Desativar" if u["active"] else "Ativar"
    pin_badge = ' <span class="badge">📌 fixado</span>' if u.get("drive_account_pinned") else ""
    drive_label = escape(u.get("drive_label") or "—")

    current_drive_id = str(u.get("drive_account_id") or "")
    reassign_options = "\n".join(
        f'<option value="{d["id"]}"{" selected" if str(d["id"]) == current_drive_id else ""}>'
        f'{escape(d["label"])}</option>'
        for d in connected_drives
    )
    auto_button = (
        f'<form class="inline" method="post" action="/admin/users/{u["id"]}/auto-assign">'
        f'<button type="submit" class="ghost sm" title="Volta a ser rebalanceado automaticamente">Auto</button>'
        f'</form>'
        if u.get("drive_account_pinned") else ""
    )
    reset_password_btn = (
        f'<form class="inline" method="post" action="/admin/users/{u["id"]}/reset-password" '
        f'onsubmit="return confirm(\'Resetar a senha? A pessoa precisa definir uma nova pelo link de convite.\')">'
        f'<button type="submit" class="ghost sm">Resetar</button></form>'
        if u.get("has_password") else ""
    )

    return f"""
    <tr>
      <td data-label="Conta"><span class="dot {status_dot}"></span>{escape(u['email'])}
          {f'<div class="name">{name}</div>' if name else ''}
          <div class="status-label">{status_label}</div>
          <details style="margin-top:.4rem">
            <summary style="cursor:pointer;color:var(--accent-2);font-size:.78rem">Editar</summary>
            <form method="post" action="/admin/users/{u['id']}/edit"
                  style="display:flex;flex-direction:column;gap:.4rem;margin-top:.5rem;max-width:16rem">
              <input type="email" name="email" value="{escape(u['email'])}" required>
              <input type="text" name="display_name" placeholder="Nome" value="{name}">
              <label style="font-size:.75rem;color:var(--dim)">Expira em
                <input type="date" name="expires_at"
                       value="{u['expires_at'].strftime('%Y-%m-%d') if u.get('expires_at') else ''}">
              </label>
              <label style="font-size:.78rem;display:flex;align-items:center;gap:.35rem">
                <input type="checkbox" name="no_expiration" style="width:auto"
                       {"checked" if not u.get('expires_at') else ""}>
                Sem prazo de expiração
              </label>
              <button type="submit" class="sm">Salvar</button>
            </form>
          </details>
      </td>
      <td data-label="Drive">{drive_label}{pin_badge}
        <form class="inline" method="post" action="/admin/users/{u['id']}/reassign" style="display:block;margin-top:.35rem">
          <select name="drive_account_id" style="font-size:.78rem;padding:.25rem">
            {reassign_options}
          </select>
          <button type="submit" class="ghost sm" title="Fixa nessa conta">Fixar</button>
        </form>
        {auto_button}
      </td>
      <td data-label="Dispositivo">{_format_device(str(u['id']))}</td>
      <td data-label="Senha">{senha}<br>{reset_password_btn}</td>
      <td data-label="Expira">{expiry_html}</td>
      <td data-label="Convite / Ações">
        <a href="{invite_url}" target="_blank"><code>{invite_url}</code></a>
        <div class="row-actions" style="margin-top:.4rem">
          <form class="inline" method="post" action="/admin/users/{u['id']}/renew">
            <input type="hidden" name="days" value="30">
            <button type="submit" class="ghost sm" title="Renovar por mais 30 dias">+30d</button>
          </form>
          <form class="inline" method="post" action="/admin/users/{u['id']}/toggle">
            <button type="submit" class="ghost sm">{toggle_label}</button>
          </form>
          <form class="inline" method="post" action="/admin/users/{u['id']}/delete"
                onsubmit="return confirm('Remover {escape(u['email'])}? A URL do addon dele para de funcionar imediatamente.')">
            <button type="submit" class="danger sm">Remover</button>
          </form>
        </div>
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
        abort(400)

    expires_at = None
    if not clear_expiration and expires_raw:
        try:
            expires_at = datetime.strptime(expires_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            abort(400)

    db.update_user(uid, email, display_name, expires_at, clear_expiration=clear_expiration)
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
    """Pins this family account to a specific pool account."""
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


@app.route("/admin/users/<uid>/free-device", methods=["POST"])
@require_admin
def admin_free_device(uid):
    db.clear_device_session(uid)
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
        '<tr><td colspan="4" class="muted">Nenhuma conta Drive ainda.</td></tr>'
    )

    cf_configured = bool(os.environ.get("CF_PROXY_URL"))
    proxy_on = db.get_setting("cf_proxy_enabled", default="1") != "0"
    if not cf_configured:
        proxy_toggle = (
            '<p class="lede" style="color:var(--warn)">⚠️ CF_PROXY_URL não está '
            'configurada na Vercel - esse botão não tem efeito até você definir a env var.</p>'
        )
    else:
        dot = "ok" if proxy_on else ""
        label = "Ativado" if proxy_on else "Desativado"
        action_label = "Desativar" if proxy_on else "Ativar"
        proxy_toggle = f"""
        <div class="panel" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.7rem">
          <div>
            <div><span class="dot {dot}"></span><strong>Proxy Cloudflare: {label}</strong></div>
            <div class="status-label">Quando desativado, todo mundo passa a receber o vídeo
            direto da API do Google (sem cache, sem esconder o token) até você reativar.</div>
          </div>
          <form method="post" action="/admin/settings/proxy/toggle">
            <button type="submit" class="{'ghost' if proxy_on else ''}">{action_label}</button>
          </form>
        </div>
        """

    return _page(f"""
      <h1>Contas Drive</h1>
      <p class="lede">Todas devem apontar pra mesma pasta compartilhada. Novas
      contas de família são atribuídas automaticamente a quem tiver menos gente.
      <a href="/admin/drives/worker-config">Ver configuração do Worker →</a></p>
      {proxy_toggle}
      <div class="panel">
        <form class="create" method="post" action="/admin/drives">
          <input type="text" name="label" placeholder="Nome (ex: Drive 1)" required>
          <button type="submit">Adicionar</button>
        </form>
      </div>
      <div class="panel">
        <table>
          <tr><th>Conta</th><th>Status</th><th>Famílias</th><th>Ações</th></tr>
          {rows}
        </table>
      </div>
    """, title="Contas Drive - Admin", active="drives")


@app.route("/admin/settings/proxy/toggle", methods=["POST"])
@require_admin
def admin_toggle_proxy():
    current = db.get_setting("cf_proxy_enabled", default="1") != "0"
    db.set_setting("cf_proxy_enabled", "0" if current else "1")
    return redirect("/admin/drives")


def _drive_row(d):
    if not d["connected"]:
        status = '<span class="dot warn"></span>não conectado'
        detail = ""
    elif not d["active"]:
        status = '<span class="dot"></span>desativado'
        detail = ""
    else:
        status = '<span class="dot ok"></span>conectado'
        live = _live_drive_status(d["id"])
        if live and live.get("connected"):
            usage = live.get("usage_human", "?")
            limit = live.get("limit_human")
            pct = live.get("usage_pct")
            bar = (
                f'<div style="height:5px;border-radius:3px;background:var(--bg-soft);'
                f'margin-top:.3rem;overflow:hidden"><div style="height:100%;width:{pct}%;'
                f'background:linear-gradient(90deg,var(--accent-2),var(--accent))"></div></div>'
                if pct is not None else ""
            )
            detail = (
                f'<div class="status-label">{escape(live.get("account") or "")}</div>'
                f'<div class="status-label">{usage}{" de " + limit if limit else ""} em uso</div>'
                f'{bar}'
            )
        elif live:
            detail = f'<div class="status-label" style="color:var(--err)">Sem resposta do Drive ({escape(live.get("error",""))})</div>'
        else:
            detail = ""

    connect_label = "Reconectar" if d["connected"] else "Conectar ao Google"
    toggle_label = "Desativar" if d["active"] else "Ativar"

    return f"""
    <tr>
      <td data-label="Conta">{escape(d['label'])}</td>
      <td data-label="Status">{status}{detail}</td>
      <td data-label="Famílias">{d['assigned_count']}</td>
      <td data-label="Ações">
        <div class="row-actions">
          <a href="/admin/drives/{d['id']}/connect-google" class="btn-link">{connect_label}</a>
          <form class="inline" method="post" action="/admin/drives/{d['id']}/toggle">
            <button type="submit" class="ghost sm">{toggle_label}</button>
          </form>
          <form class="inline" method="post" action="/admin/drives/{d['id']}/delete"
                onsubmit="return confirm('Remover {escape(d['label'])}? As {d['assigned_count']} famílias nela serão redistribuídas automaticamente entre as demais contas do pool.')">
            <button type="submit" class="danger sm">Remover</button>
          </form>
        </div>
      </td>
    </tr>
    """


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

    pretty = escape(_json.dumps(config, indent=2, ensure_ascii=False), quote=False)

    shared_secret_set = bool(os.environ.get("PROXY_SHARED_SECRET"))
    if shared_secret_set:
        secret_status = (
            '<span class="dot ok"></span> <code>PROXY_SHARED_SECRET</code> está '
            "definido nesta aplicação."
        )
    else:
        secret_status = (
            '<span class="dot"></span> <code>PROXY_SHARED_SECRET</code> <strong>não</strong> '
            "está definido - enquanto isso, o endpoint responde 503 e o Worker não "
            "consegue pegar token por aqui."
        )

    token_endpoint = escape(f"https://{request.host}/internal/drive-token")

    return _page(f"""
      <h1>Configuração do Worker</h1>

      <div class="panel">
        <h3>Recomendado: o Worker pede o token para cá</h3>
        <p class="lede">Assim o <code>refresh_token</code> de cada conta fica só no banco
        desta aplicação. O Worker não guarda credencial nenhuma, e não tem como
        as duas cópias ficarem fora de sincronia - foi exatamente isso que
        derrubou o proxy antes (Worker com token velho → 502 em tudo).</p>
        <p>{secret_status}</p>
        <p>No Worker (Settings → Variables):</p>
        <pre style="white-space:pre-wrap;word-break:break-all;font-size:.78rem;color:var(--txt);
                    background:var(--bg-soft);padding:1rem;border-radius:.5rem">TOKEN_ENDPOINT        (texto)  {token_endpoint}
TOKEN_ENDPOINT_SECRET (secret) mesmo valor de PROXY_SHARED_SECRET</pre>
        <p class="lede">Com <code>TOKEN_ENDPOINT</code> definido, o <code>ACCOUNTS</code>
        abaixo deixa de ser usado e pode ser apagado.</p>
      </div>

      <div class="panel">
        <h3>Alternativa: credenciais dentro do Worker</h3>
        <p class="lede">Cole isso como o secret <code>ACCOUNTS</code> do Worker Cloudflare
        (Settings → Variables → Add → tipo Secret). Contém segredos - não compartilhe.
        Só é lido quando <code>TOKEN_ENDPOINT</code> não está definido, e precisa ser
        colado de novo toda vez que você reconectar uma conta em /admin/drives.</p>
        <pre style="white-space:pre-wrap;word-break:break-all;font-size:.78rem;color:var(--txt);
                    background:var(--bg-soft);padding:1rem;border-radius:.5rem">{pretty}</pre>
      </div>
    """, title="Config do Worker - Admin", active="drives")


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
    rows = "\n".join(_tmdb_row(k, decrypt(k["api_key"])) for k in keys) or (
        '<tr><td colspan="3" class="muted">Nenhuma chave TMDB ainda.</td></tr>'
    )

    return _page(f"""
      <h1>Chaves TMDB</h1>
      <p class="lede">Usadas pra resolver título/ano/pt-BR dos streams. Cadastre
      mais de uma pra dividir o limite de requisições da TMDB entre elas - a
      escolha por chamada é aleatória entre as ativas.</p>
      <div class="panel">
        <form class="create" method="post" action="/admin/tmdb">
          <input type="text" name="label" placeholder="Nome (ex: Chave 1)" required>
          <input type="text" name="api_key" placeholder="Chave da API TMDB" required>
          <button type="submit">Adicionar</button>
        </form>
      </div>
      <div class="panel">
        <table>
          <tr><th>Nome</th><th>Chave</th><th>Ações</th></tr>
          {rows}
        </table>
      </div>
      <p class="lede">Pegue sua chave em
      <a href="https://www.themoviedb.org/settings/api" target="_blank">themoviedb.org/settings/api</a>.</p>
    """, title="Chaves TMDB - Admin", active="tmdb")


def _tmdb_row(k, plain_key):
    status = '<span class="dot ok"></span>ativa' if k["active"] else '<span class="dot"></span>desativada'
    toggle_label = "Desativar" if k["active"] else "Ativar"

    return f"""
    <tr>
      <td data-label="Nome">{escape(k['label'])}<div class="status-label">{status}</div></td>
      <td data-label="Chave"><code>{escape(_mask_key(plain_key))}</code></td>
      <td data-label="Ações">
        <div class="row-actions">
          <form class="inline" method="post" action="/admin/tmdb/{k['id']}/toggle">
            <button type="submit" class="ghost sm">{toggle_label}</button>
          </form>
          <form class="inline" method="post" action="/admin/tmdb/{k['id']}/delete"
                onsubmit="return confirm('Remover a chave {escape(k['label'])}?')">
            <button type="submit" class="danger sm">Remover</button>
          </form>
        </div>
      </td>
    </tr>
    """


@app.route("/admin/tmdb", methods=["POST"])
@require_admin
def admin_create_tmdb():
    label = (request.form.get("label") or "").strip()
    api_key = (request.form.get("api_key") or "").strip()
    if not label or not api_key:
        abort(400)
    db.create_tmdb_key(label, api_key)
    return redirect("/admin/tmdb")


@app.route("/admin/tmdb/<tid>/toggle", methods=["POST"])
@require_admin
def admin_toggle_tmdb(tid):
    key_row = db.get_tmdb_key(tid)
    if not key_row:
        abort(404)
    db.set_tmdb_key_active(tid, not key_row["active"])
    return redirect("/admin/tmdb")


@app.route("/admin/tmdb/<tid>/delete", methods=["POST"])
@require_admin
def admin_delete_tmdb(tid):
    db.delete_tmdb_key(tid)
    return redirect("/admin/tmdb")
