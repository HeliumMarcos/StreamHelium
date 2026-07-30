import uuid

import pytest
from flask import g

from sgd import app

TEST_USER_ID = str(uuid.uuid4())


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _login(client):
    return client.post("/admin/login", data={"password": "test-admin-password"})


# --- TMDB key pool (admin) -------------------------------------------------

def test_admin_tmdb_list(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_tmdb_keys", lambda: [{
        "id": "k1", "label": "Chave 1", "active": True,
        "api_key": "encrypted-blob",
    }])
    monkeypatch.setattr("sgd.crypto.decrypt", lambda v: "abcd1234efgh5678")
    _login(client)

    resp = client.get("/admin/tmdb")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Chave 1" in body
    assert "5678" in body  # last 4 chars visible
    assert "abcd1234efgh5678" not in body  # rest is masked


def test_admin_create_tmdb_key(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sgd.db.create_tmdb_key",
        lambda label, api_key: calls.append((label, api_key)),
    )
    _login(client)

    resp = client.post("/admin/tmdb", data={"label": "Chave 2", "api_key": "xyz789"})

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/tmdb"
    assert calls == [("Chave 2", "xyz789")]


def test_admin_create_tmdb_key_requires_both_fields(client):
    _login(client)
    resp = client.post("/admin/tmdb", data={"label": "Sem chave"})
    assert resp.status_code == 400


def test_admin_toggle_tmdb_key(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.get_tmdb_key", lambda tid: {"id": tid, "active": True}
    )
    calls = []
    monkeypatch.setattr(
        "sgd.db.set_tmdb_key_active", lambda tid, active: calls.append((tid, active))
    )
    _login(client)

    resp = client.post("/admin/tmdb/k1/toggle")

    assert resp.status_code == 302
    assert calls == [("k1", False)]


def test_admin_delete_tmdb_key(client, monkeypatch):
    calls = []
    monkeypatch.setattr("sgd.db.delete_tmdb_key", lambda tid: calls.append(tid))
    _login(client)

    resp = client.post("/admin/tmdb/k1/delete")

    assert resp.status_code == 302
    assert calls == ["k1"]


def test_admin_tmdb_requires_login(client):
    resp = client.get("/admin/tmdb")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/login"


def test_admin_free_device(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sgd.db.clear_device_session", lambda uid: calls.append(uid)
    )
    _login(client)

    resp = client.post("/admin/users/abc/free-device")

    assert resp.status_code == 302
    assert calls == ["abc"]


# --- Cloudflare proxy toggle -------------------------------------------

def test_proxy_toggle_enabled_by_default(monkeypatch):
    import sgd.streams as streams
    streams._proxy_toggle_cache["fetched_at"] = 0.0
    monkeypatch.setattr("sgd.db.get_setting", lambda key, default=None: default)
    assert streams.proxy_toggle_enabled() is True
    streams._proxy_toggle_cache["fetched_at"] = 0.0


def test_proxy_toggle_disabled_when_setting_is_zero(monkeypatch):
    import sgd.streams as streams
    streams._proxy_toggle_cache["fetched_at"] = 0.0
    monkeypatch.setattr("sgd.db.get_setting", lambda key, default=None: "0")
    assert streams.proxy_toggle_enabled() is False
    streams._proxy_toggle_cache["fetched_at"] = 0.0


def test_proxy_toggle_fails_open_on_db_error(monkeypatch):
    import sgd.streams as streams
    streams._proxy_toggle_cache["fetched_at"] = 0.0

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("sgd.db.get_setting", boom)

    assert streams.proxy_toggle_enabled() is True
    streams._proxy_toggle_cache["fetched_at"] = 0.0

def test_meta_tmdb_api_key_uses_pool(monkeypatch):
    import sgd.meta as meta
    meta._tmdb_pool["fetched_at"] = 0.0
    monkeypatch.setattr(
        "sgd.db.list_active_tmdb_keys_decrypted", lambda: ["only-key"]
    )
    assert meta._tmdb_api_key() == "only-key"
    meta._tmdb_pool["fetched_at"] = 0.0  # reset for other tests


def test_meta_tmdb_api_key_falls_back_to_env(monkeypatch):
    import sgd.meta as meta
    meta._tmdb_pool["keys"] = []
    meta._tmdb_pool["fetched_at"] = 1e15  # far future -> skip DB refresh
    monkeypatch.setenv("TMDB_API_KEY", "env-fallback-key")
    assert meta._tmdb_api_key() == "env-fallback-key"
    meta._tmdb_pool["fetched_at"] = 0.0  # reset for other tests


# --- device limit on the stream endpoint -----------------------------------

def _fake_user_row(**overrides):
    row = {"id": TEST_USER_ID, "email": "t@example.com", "active": True}
    row.update(overrides)
    return row


def _patch_tenant(monkeypatch, gdrive=None):
    user_row = _fake_user_row()

    def fake_load_tenant(user_id):
        g.user = user_row
        g.gdrive = gdrive
        return user_row

    monkeypatch.setattr("sgd.tenancy.load_tenant", fake_load_tenant)


def test_stream_blocked_when_device_limit_reached(client, monkeypatch):
    _patch_tenant(monkeypatch)
    monkeypatch.setattr("sgd.db.touch_device_session", lambda *a, **k: False)

    resp = client.get(f"/u/{TEST_USER_ID}/stream/movie/tt0111161.json")

    assert resp.status_code == 200
    assert resp.get_json() == {"streams": []}


def test_stream_allowed_when_device_matches(client, monkeypatch):
    _patch_tenant(monkeypatch, gdrive=object())
    monkeypatch.setattr("sgd.db.touch_device_session", lambda *a, **k: True)
    monkeypatch.setattr("sgd.routes.get_streams", lambda gdrive, t, sid: iter(['{"streams":[]}']))

    resp = client.get(f"/u/{TEST_USER_ID}/stream/movie/tt0111161.json")

    assert resp.status_code == 200
    assert resp.get_json() == {"streams": []}


def test_device_check_fails_open_on_db_error(client, monkeypatch):
    """A DB hiccup on the device check must not break playback for
    everyone - it should allow the request through."""
    _patch_tenant(monkeypatch, gdrive=object())
    monkeypatch.setattr("sgd.routes.get_streams", lambda gdrive, t, sid: iter(['{"streams":[]}']))

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("sgd.db.touch_device_session", boom)

    resp = client.get(f"/u/{TEST_USER_ID}/stream/movie/tt0111161.json")

    assert resp.status_code == 200
    assert resp.get_json() == {"streams": []}
