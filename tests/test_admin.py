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
            "drive_connected": True,
            "tmdb_connected": False,
        }],
    )
    _login(client)

    resp = client.get("/admin")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "user@example.com" in body
    assert "/connect/abc123" in body


def test_admin_create_user_calls_db(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sgd.db.create_user",
        lambda email, display_name: calls.append((email, display_name)),
    )
    _login(client)

    resp = client.post(
        "/admin/users", data={"email": "Novo@Example.com", "display_name": "Novo"}
    )

    assert resp.status_code == 302
    assert calls == [("novo@example.com", "Novo")]


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


def test_admin_delete_user(client, monkeypatch):
    calls = []
    monkeypatch.setattr("sgd.db.delete_user", lambda uid: calls.append(uid))
    _login(client)

    resp = client.post("/admin/users/abc/delete")

    assert resp.status_code == 302
    assert calls == ["abc"]


def test_admin_routes_require_login_even_with_valid_data(client, monkeypatch):
    """Regression guard: every /admin/* mutation must go through
    require_admin, not just /admin itself."""
    resp = client.post("/admin/users", data={"email": "x@example.com"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/login"
