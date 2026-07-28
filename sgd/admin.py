"""Admin panel - manage which users have access to the addon.

Auth: a single shared password in the ADMIN_PASSWORD env var. This is
intentionally simple (matches how the person deploying this app just wants
to gate one panel for themselves), not meant to scale to multiple admins.
"""

import functools
import logging
import os
from html import escape

from flask import abort, jsonify, redirect, request, session, Response

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
           --txt:#ece8f4; --dim:#a79fbb; --accent:#8b5cf6; --ok:#34d399; --off:#6b7280; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--txt); padding:2rem 1rem;
          font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
  main {{ max-width:52rem; margin:0 auto; }}
  h1 {{ font-size:1.3rem; }}
  table {{ width:100%; border-collapse:collapse; margin-top:1rem; }}
  th, td {{ text-align:left; padding:.6rem .5rem; border-bottom:1px solid var(--brd); font-size:.9rem; }}
  th {{ color:var(--dim); font-weight:600; }}
  input {{ padding:.5rem; border-radius:.4rem; border:1px solid #332844;
           background:#120c1c; color:var(--txt); }}
  button {{ padding:.5rem 1rem; border-radius:.4rem; border:none; cursor:pointer;
            background:var(--accent); color:#fff; font-weight:600; }}
  button.danger {{ background:#ef4444; }}
  button.ghost {{ background:transparent; border:1px solid var(--brd); color:var(--txt); }}
  a {{ color:var(--accent); }}
  .dot {{ display:inline-block; width:.5rem; height:.5rem; border-radius:50%; margin-right:.35rem; }}
  .dot.ok {{ background:var(--ok); }}
  .dot.off {{ background:var(--off); }}
  code {{ background:#120c1c; padding:.15rem .4rem; border-radius:.3rem; font-size:.8rem; }}
  form.inline {{ display:inline; }}
</style>
<main>{body}</main>
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

    return _page(f"""
      <h1>Entrar</h1>
      {error}
      <form method="post">
        <input type="password" name="password" placeholder="Senha do admin" autofocus>
        <button type="submit">Entrar</button>
      </form>
    """, title="Login - Admin")


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect("/admin/login")


@app.route("/admin")
@require_admin
def admin_home():
    users = db.list_users()
    rows = "\n".join(_user_row(u) for u in users) or (
        '<tr><td colspan="5" style="color:var(--dim)">Nenhum usuário ainda.</td></tr>'
    )

    return _page(f"""
      <h1>Usuários</h1>
      <form method="post" action="/admin/users" style="display:flex;gap:.5rem;margin:1rem 0">
        <input type="email" name="email" placeholder="email@exemplo.com" required style="flex:1">
        <input type="text" name="display_name" placeholder="Nome (opcional)" style="flex:1">
        <button type="submit">Adicionar</button>
      </form>
      <table>
        <tr><th>Usuário</th><th>Drive</th><th>TMDB</th><th>Convite</th><th></th></tr>
        {rows}
      </table>
      <form method="post" action="/admin/logout" style="margin-top:2rem">
        <button type="submit" class="ghost">Sair</button>
      </form>
    """)


def _user_row(u):
    status_dot = "ok" if u["active"] else "off"
    status_label = "ativo" if u["active"] else "desativado"
    drive = '<span class="dot ok"></span>conectado' if u["drive_connected"] else \
            '<span class="dot off"></span>pendente'
    tmdb = "sim" if u["tmdb_connected"] else "—"
    invite_url = f"/connect/{u['invite_token']}"
    name = escape(u["display_name"] or "")
    toggle_label = "Desativar" if u["active"] else "Ativar"

    return f"""
    <tr>
      <td><span class="dot {status_dot}"></span>{escape(u['email'])}
          {f'<br><span style="color:var(--dim);font-size:.8rem">{name}</span>' if name else ''}
          <br><span style="color:var(--dim);font-size:.75rem">{status_label}</span></td>
      <td>{drive}</td>
      <td>{tmdb}</td>
      <td><a href="{invite_url}" target="_blank"><code>{invite_url}</code></a></td>
      <td style="white-space:nowrap">
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
    if not email or "@" not in email:
        abort(400)
    try:
        db.create_user(email, display_name)
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


@app.route("/admin/users/<uid>/delete", methods=["POST"])
@require_admin
def admin_delete_user(uid):
    from sgd.tenancy import _drive_cache
    db.delete_user(uid)
    _drive_cache.pop(str(uid), None)
    return redirect("/admin")
