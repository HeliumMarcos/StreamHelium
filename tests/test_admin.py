import re

import psycopg
import pytest

from sgd import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _login(client, password="test-admin-password"):
    page = client.get("/admin/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    resp = client.post("/admin/login", data={"password": password, "csrf_token": token})
    if resp.status_code == 302:
        with client.session_transaction() as sess:
            client.environ_base["HTTP_X_CSRF_TOKEN"] = sess["admin_csrf_token"]
    return resp


def test_admin_requires_login(client):
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/login"


def test_admin_login_wrong_password(client):
    resp = _login(client, "nope")
    assert resp.status_code == 200
    assert "incorreta" in resp.get_data(as_text=True)


def test_admin_login_has_accessible_password_field(client):
    body = client.get("/admin/login").get_data(as_text=True)

    assert '<label for="admin-password">' in body
    assert 'autocomplete="current-password"' in body
    assert "Área restrita de administração" in body


def test_admin_login_correct_password(client):
    resp = _login(client)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin"


def test_admin_home_lists_users(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.list_users",
        lambda *a, **k: [{
            "id": "11111111-1111-1111-1111-111111111111",
            "email": "user@example.com",
            "display_name": "Fulano",
            "active": True,
            "invite_token": "abc123",
            "drive_account_id": "22222222-2222-2222-2222-222222222222",
            "drive_label": "Drive 1",
            "expires_at": None,
            "has_password": False,
        }],
    )
    monkeypatch.setattr("sgd.db.list_drive_accounts", lambda: [{
        "id": "22222222-2222-2222-2222-222222222222",
        "label": "Drive 1",
        "active": True,
        "connected": True,
        "assigned_count": 1,
    }])
    monkeypatch.setattr("sgd.db.get_admin_overview", lambda: {
        "family": {"total": 1, "active_count": 1, "expired_count": 0, "disabled_count": 0},
        "drives": {"total": 1, "connected": 1},
        "tmdb": {"total": 0, "active_count": 0},
        "devices": {"total": 0},
    })
    monkeypatch.setattr("sgd.db.get_device_session", lambda uid: None)
    _login(client)

    resp = client.get("/admin")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "user@example.com" in body
    assert "Drive 1" in body
    assert "/connect/abc123" in body
    assert 'aria-label="Seções administrativas"' in body
    assert 'aria-current="page"' in body
    assert 'src="/static/admin.js"' in body
    assert 'data-copy-url="/connect/abc123"' in body
    assert "Copiar link" in body
    assert 'name="csrf_token"' in body
    assert 'data-confirm="Remover user@example.com?' in body
    assert "onsubmit=" not in body


def test_admin_create_user_calls_db(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.pick_least_loaded_drive_account",
        lambda: {"id": "22222222-2222-2222-2222-222222222222", "label": "Drive 1"},
    )
    calls = []
    monkeypatch.setattr(
        "sgd.db.create_user",
        lambda email, display_name, drive_account_id=None, expires_in_days=None, pinned=False, idempotency_key=None:
            calls.append((email, display_name, drive_account_id, expires_in_days, pinned)),
    )
    _login(client)

    resp = client.post(
        "/admin/users",
        data={"email": "Novo@Example.com", "display_name": "Novo", "expires_in_days": "30"},
    )

    assert resp.status_code == 302
    assert calls == [
        ("novo@example.com", "Novo", "22222222-2222-2222-2222-222222222222", 30, False)
    ]


def test_admin_create_user_with_pinned_drive(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sgd.db.create_user",
        lambda email, display_name, drive_account_id=None, expires_in_days=None, pinned=False, idempotency_key=None:
            calls.append((email, display_name, drive_account_id, expires_in_days, pinned)),
    )
    _login(client)

    resp = client.post(
        "/admin/users",
        data={"email": "fixo@example.com", "drive_account_id": "d1"},
    )

    assert resp.status_code == 302
    assert calls == [("fixo@example.com", None, "d1", None, True)]


def test_admin_create_user_reports_missing_email(client):
    _login(client)
    resp = client.post("/admin/users", data={"display_name": "Sem email"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin"
    with client.session_transaction() as sess:
        assert any("e-mail válido" in message for _, message in sess["_flashes"])


def test_admin_login_rejects_missing_csrf(client):
    resp = client.post("/admin/login", data={"password": "test-admin-password"})

    assert resp.status_code == 400
    with client.session_transaction() as sess:
        assert not sess.get("is_admin")


def test_admin_mutation_rejects_missing_csrf(client, monkeypatch):
    _login(client)
    client.environ_base.pop("HTTP_X_CSRF_TOKEN")
    monkeypatch.setattr("sgd.db.delete_user", lambda uid: pytest.fail("must not mutate"))

    resp = client.post("/admin/users/abc/delete")

    assert resp.status_code == 400


def test_admin_create_user_reports_database_failure(client, monkeypatch):
    monkeypatch.setattr("sgd.db.pick_least_loaded_drive_account", lambda: None)

    def boom(*args, **kwargs):
        # psycopg.Error e nao RuntimeError: so erro de banco vira "verifique
        # o e-mail". Erro de programacao tem que estourar.
        raise psycopg.errors.UniqueViolation("duplicate key")

    monkeypatch.setattr("sgd.db.create_user", boom)
    _login(client)

    resp = client.post("/admin/users", data={"email": "existente@example.com"})

    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert any("Não foi possível criar" in message for _, message in sess["_flashes"])


def test_admin_toggle_user(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.get_user",
        lambda uid: {"id": uid, "active": True},
    )
    calls = []
    monkeypatch.setattr(
        "sgd.db.set_active", lambda uid, active: calls.append((uid, active))
    )
    _login(client)

    resp = client.post("/admin/users/abc/toggle")

    assert resp.status_code == 302
    assert calls == [("abc", False)]


def test_admin_toggle_missing_user_404s(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: None)
    _login(client)

    resp = client.post("/admin/users/missing/toggle")

    assert resp.status_code == 404


def test_admin_reassign_user_pins_drive(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: {"id": uid})
    monkeypatch.setattr("sgd.db.get_drive_account", lambda did: {"id": did, "active": True})
    calls = []
    monkeypatch.setattr(
        "sgd.db.reassign_drive_account",
        lambda uid, drive_account_id, pinned=False: calls.append((uid, drive_account_id, pinned)),
    )
    _login(client)

    resp = client.post("/admin/users/abc/reassign", data={"drive_account_id": "d2"})

    assert resp.status_code == 302
    assert calls == [("abc", "d2", True)]


def test_admin_reassign_user_reports_missing_drive_account_id(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: {"id": uid})
    _login(client)

    resp = client.post("/admin/users/abc/reassign", data={})

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin"


def test_admin_auto_assign_user_unpins_and_rebalances(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: {"id": uid})
    monkeypatch.setattr(
        "sgd.db.pick_least_loaded_drive_account", lambda: {"id": "d3", "label": "Drive 3"}
    )
    calls = []
    monkeypatch.setattr(
        "sgd.db.reassign_drive_account",
        lambda uid, drive_account_id, pinned=False: calls.append((uid, drive_account_id, pinned)),
    )
    _login(client)

    resp = client.post("/admin/users/abc/auto-assign")

    assert resp.status_code == 302
    assert calls == [("abc", "d3", False)]


def test_admin_edit_user_updates_fields(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: {"id": uid})
    calls = []
    monkeypatch.setattr(
        "sgd.db.update_user",
        lambda uid, email, display_name, expires_at, clear_expiration=False:
            calls.append((uid, email, display_name, expires_at, clear_expiration)),
    )
    _login(client)

    resp = client.post(
        "/admin/users/abc/edit",
        data={"email": "Novo@Example.com", "display_name": "Novo Nome",
              "expires_at": "2026-12-31"},
    )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin"
    uid, email, name, expires_at, clear = calls[0]
    assert email == "novo@example.com"
    assert name == "Novo Nome"
    assert expires_at.year == 2026 and expires_at.month == 12 and expires_at.day == 31
    assert clear is False


def test_admin_edit_user_no_expiration(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: {"id": uid})
    calls = []
    monkeypatch.setattr(
        "sgd.db.update_user",
        lambda uid, email, display_name, expires_at, clear_expiration=False:
            calls.append((uid, email, display_name, expires_at, clear_expiration)),
    )
    _login(client)

    resp = client.post(
        "/admin/users/abc/edit",
        data={"email": "sempre@example.com", "no_expiration": "on"},
    )

    assert resp.status_code == 302
    assert calls == [("abc", "sempre@example.com", None, None, True)]


def test_admin_edit_user_reports_invalid_email(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: {"id": uid})
    _login(client)

    resp = client.post("/admin/users/abc/edit", data={"email": "not-an-email"})

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin"


def test_admin_edit_user_missing_user_404s(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: None)
    _login(client)

    resp = client.post("/admin/users/missing/edit", data={"email": "x@example.com"})

    assert resp.status_code == 404


def test_admin_reset_password(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: {"id": uid})
    calls = []
    monkeypatch.setattr("sgd.db.clear_password", lambda uid: calls.append(uid))
    _login(client)

    resp = client.post("/admin/users/abc/reset-password")

    assert resp.status_code == 302
    assert calls == ["abc"]


def test_admin_delete_user(client, monkeypatch):
    calls = []
    monkeypatch.setattr("sgd.db.get_user", lambda uid: {"id": uid})
    monkeypatch.setattr("sgd.db.delete_user", lambda uid: calls.append(uid))
    _login(client)

    resp = client.post("/admin/users/abc/delete")

    assert resp.status_code == 302
    assert calls == ["abc"]


def test_admin_delete_missing_user_404s(client, monkeypatch):
    """Antes isso respondia "conta removida" para um id que nunca existiu,
    o que e uma mentira silenciosa. A API JSON precisa distinguir os dois
    casos, e o painel passou a distinguir junto."""
    calls = []
    monkeypatch.setattr("sgd.db.get_user", lambda uid: None)
    monkeypatch.setattr("sgd.db.delete_user", lambda uid: calls.append(uid))
    _login(client)

    resp = client.post("/admin/users/nao-existe/delete")

    assert resp.status_code == 404
    assert calls == []


def test_admin_renew_user(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: {"id": uid})
    calls = []
    monkeypatch.setattr(
        "sgd.db.renew_user", lambda uid, days: calls.append((uid, days))
    )
    _login(client)

    resp = client.post("/admin/users/abc/renew", data={"days": "30"})

    assert resp.status_code == 302
    assert calls == [("abc", 30)]


def test_admin_drives_list_shows_live_status(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_drive_accounts", lambda: [{
        "id": "d1", "label": "Drive 1", "active": True,
        "connected": True, "assigned_count": 2,
    }])
    monkeypatch.setattr("sgd.db.get_setting", lambda key, default=None: default)

    class FakeGoogleDrive:
        pass

    monkeypatch.setattr("sgd.tenancy._get_drive_instance", lambda did: FakeGoogleDrive())
    monkeypatch.setattr(
        "sgd.routes.drive_status",
        lambda gdrive: {
            "connected": True, "account": "h****z@gmail.com",
            "usage_human": "10.0GiB", "limit_human": "100.0GiB", "usage_pct": 10.0,
        },
    )
    _login(client)

    resp = client.get("/admin/drives")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "h****z@gmail.com" in body
    assert "10.0GiB" in body


def test_admin_drives_marks_expired_authorization_as_reconnect_required(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_drive_accounts", lambda: [{
        "id": "d1", "label": "Drive 1", "active": True,
        "connected": True, "assigned_count": 2,
    }])
    monkeypatch.setattr("sgd.db.get_setting", lambda key, default=None: default)
    monkeypatch.setattr(
        "sgd.admin._live_drive_status",
        lambda did: {
            "connected": False,
            "error_code": "authorization_expired",
            "error": "Autorização do Google expirada ou revogada. Reconecte esta conta.",
            "reconnect_required": True,
        },
    )
    _login(client)

    body = client.get("/admin/drives").get_data(as_text=True)

    assert "reconexão necessária" in body
    assert "Autorização do Google expirada ou revogada" in body
    assert "Sem resposta do Drive (RefreshError)" not in body


def test_admin_drives_list(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_drive_accounts", lambda: [{
        "id": "d1", "label": "Drive 1", "active": True,
        "connected": False, "assigned_count": 0,
    }])
    monkeypatch.setattr("sgd.db.get_setting", lambda key, default=None: default)
    _login(client)

    resp = client.get("/admin/drives")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Drive 1" in body
    assert "Conectar ao Google" in body


def test_admin_create_drive(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sgd.db.create_drive_account", lambda label: calls.append(label)
    )
    _login(client)

    resp = client.post("/admin/drives", data={"label": "Drive 2"})

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/drives"
    assert calls == ["Drive 2"]


def test_admin_create_drive_reports_empty_label(client):
    _login(client)
    resp = client.post("/admin/drives", data={"label": "  "})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/drives"


def test_admin_toggle_drive(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.get_drive_account", lambda did: {"id": did, "active": True}
    )
    calls = []
    monkeypatch.setattr(
        "sgd.db.set_drive_account_active",
        lambda did, active: calls.append((did, active)),
    )
    _login(client)

    resp = client.post("/admin/drives/d1/toggle")

    assert resp.status_code == 302
    assert calls == [("d1", False)]


def test_admin_delete_drive(client, monkeypatch):
    calls = []
    monkeypatch.setattr("sgd.db.get_drive_account", lambda did: {"id": did, "active": True})
    monkeypatch.setattr(
        "sgd.db.redistribute_and_delete_drive_account",
        lambda did: calls.append(did) or 0,
    )
    _login(client)

    resp = client.post("/admin/drives/d1/delete")

    assert resp.status_code == 302
    assert calls == ["d1"]


def test_admin_delete_missing_drive_404s(client, monkeypatch):
    calls = []
    monkeypatch.setattr("sgd.db.get_drive_account", lambda did: None)
    monkeypatch.setattr(
        "sgd.db.redistribute_and_delete_drive_account",
        lambda did: calls.append(did) or 0,
    )
    _login(client)

    resp = client.post("/admin/drives/nao-existe/delete")

    assert resp.status_code == 404
    assert calls == []


def test_admin_routes_require_login_even_with_valid_data(client, monkeypatch):
    """Regression guard: every /admin/* mutation must go through
    require_admin, not just /admin itself."""
    resp = client.post("/admin/users", data={"email": "x@example.com"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/login"


def test_drive_oauth_uses_one_time_session_state(client, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    monkeypatch.setattr(
        "sgd.db.get_drive_account",
        lambda did: {"id": did, "label": "Drive 1"},
    )
    _login(client)

    resp = client.get("/admin/drives/d1/connect-google")

    assert resp.status_code == 302
    state = parse_qs(urlparse(resp.headers["Location"]).query)["state"][0]
    assert "d1" not in state
    with client.session_transaction() as sess:
        assert sess["drive_oauth_state"] == {"token": state, "drive_account_id": "d1"}

    rejected = client.get("/oauth/callback?code=fake&state=wrong")
    assert rejected.status_code == 400
    with client.session_transaction() as sess:
        assert "drive_oauth_state" not in sess


def test_admin_toggle_proxy_off_to_on(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_setting", lambda key, default=None: "0")
    calls = []
    monkeypatch.setattr(
        "sgd.db.set_setting", lambda key, value: calls.append((key, value))
    )
    _login(client)

    resp = client.post("/admin/settings/proxy/toggle")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/drives"
    assert calls == [("cf_proxy_enabled", "1")]


def test_admin_toggle_proxy_on_to_off(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_setting", lambda key, default=None: "1")
    calls = []
    monkeypatch.setattr(
        "sgd.db.set_setting", lambda key, value: calls.append((key, value))
    )
    _login(client)

    resp = client.post("/admin/settings/proxy/toggle")

    assert calls == [("cf_proxy_enabled", "0")]


def test_admin_drives_shows_proxy_warning_when_not_configured(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_drive_accounts", lambda: [])
    monkeypatch.setattr("sgd.db.get_setting", lambda key, default=None: default)
    _login(client)

    resp = client.get("/admin/drives")

    body = resp.get_data(as_text=True)
    assert "CF_PROXY_URL" in body
    assert "Nenhuma conta Drive" in body
    assert "Adicione uma conta acima" in body
    assert 'id="create-drive"' in body


def test_admin_drives_worker_config(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_drive_accounts", lambda: [
        {"id": "d1", "label": "Drive 1", "active": True, "connected": True,
         "assigned_count": 0, "connected_at": None, "created_at": None},
        {"id": "d2", "label": "Drive 2", "active": True, "connected": False,
         "assigned_count": 0, "connected_at": None, "created_at": None},
    ])
    monkeypatch.setattr(
        "sgd.db.get_drive_account",
        lambda did: {"id": did, "google_refresh_token": "encrypted-blob"},
    )
    monkeypatch.setattr("sgd.crypto.decrypt", lambda v: "real-refresh-token")
    _login(client)

    resp = client.get("/admin/drives/worker-config")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "real-refresh-token" in body
    assert "&#34;d1&#34;" in body
    assert "&#34;d2&#34;" not in body  # unconnected accounts are skipped


def test_admin_drives_require_login(client):
    resp = client.get("/admin/drives")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/login"
