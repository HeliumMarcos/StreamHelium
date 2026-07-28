import pytest

from sgd import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _login(client):
    return client.post("/admin/login", data={"password": "test-admin-password"})


def test_admin_requires_login(client):
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/login"


def test_admin_login_wrong_password(client):
    resp = client.post("/admin/login", data={"password": "nope"})
    assert resp.status_code == 200
    assert "incorreta" in resp.get_data(as_text=True)


def test_admin_login_correct_password(client):
    resp = _login(client)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin"


def test_admin_home_lists_users(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.list_users",
        lambda: [{
            "id": "11111111-1111-1111-1111-111111111111",
            "email": "user@example.com",
            "display_name": "Fulano",
            "active": True,
            "invite_token": "abc123",
            "drive_account_id": "22222222-2222-2222-2222-222222222222",
            "drive_label": "Drive 1",
            "expires_at": None,
            "tmdb_connected": False,
        }],
    )
    monkeypatch.setattr("sgd.db.list_drive_accounts", lambda: [{
        "id": "22222222-2222-2222-2222-222222222222",
        "label": "Drive 1",
        "active": True,
        "connected": True,
        "assigned_count": 1,
    }])
    _login(client)

    resp = client.get("/admin")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "user@example.com" in body
    assert "Drive 1" in body
    assert "/connect/abc123" in body


def test_admin_create_user_calls_db(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.pick_least_loaded_drive_account",
        lambda: {"id": "22222222-2222-2222-2222-222222222222", "label": "Drive 1"},
    )
    calls = []
    monkeypatch.setattr(
        "sgd.db.create_user",
        lambda email, display_name, drive_account_id=None, expires_in_days=None, pinned=False:
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
        lambda email, display_name, drive_account_id=None, expires_in_days=None, pinned=False:
            calls.append((email, display_name, drive_account_id, expires_in_days, pinned)),
    )
    _login(client)

    resp = client.post(
        "/admin/users",
        data={"email": "fixo@example.com", "drive_account_id": "d1"},
    )

    assert resp.status_code == 302
    assert calls == [("fixo@example.com", None, "d1", None, True)]


def test_admin_create_user_rejects_missing_email(client):
    _login(client)
    resp = client.post("/admin/users", data={"display_name": "Sem email"})
    assert resp.status_code == 400


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
    calls = []
    monkeypatch.setattr(
        "sgd.db.reassign_drive_account",
        lambda uid, drive_account_id, pinned=False: calls.append((uid, drive_account_id, pinned)),
    )
    _login(client)

    resp = client.post("/admin/users/abc/reassign", data={"drive_account_id": "d2"})

    assert resp.status_code == 302
    assert calls == [("abc", "d2", True)]


def test_admin_reassign_user_requires_drive_account_id(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: {"id": uid})
    _login(client)

    resp = client.post("/admin/users/abc/reassign", data={})

    assert resp.status_code == 400


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


def test_admin_delete_user(client, monkeypatch):
    calls = []
    monkeypatch.setattr("sgd.db.delete_user", lambda uid: calls.append(uid))
    _login(client)

    resp = client.post("/admin/users/abc/delete")

    assert resp.status_code == 302
    assert calls == ["abc"]


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


def test_admin_drives_list(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_drive_accounts", lambda: [{
        "id": "d1", "label": "Drive 1", "active": True,
        "connected": False, "assigned_count": 0,
    }])
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


def test_admin_create_drive_rejects_empty_label(client):
    _login(client)
    resp = client.post("/admin/drives", data={"label": "  "})
    assert resp.status_code == 400


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
    monkeypatch.setattr(
        "sgd.db.redistribute_and_delete_drive_account",
        lambda did: calls.append(did) or 0,
    )
    _login(client)

    resp = client.post("/admin/drives/d1/delete")

    assert resp.status_code == 302
    assert calls == ["d1"]


def test_admin_routes_require_login_even_with_valid_data(client, monkeypatch):
    """Regression guard: every /admin/* mutation must go through
    require_admin, not just /admin itself."""
    resp = client.post("/admin/users", data={"email": "x@example.com"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/login"


def test_admin_drives_require_login(client):
    resp = client.get("/admin/drives")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/login"
