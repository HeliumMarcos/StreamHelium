import uuid

import pytest
from flask import g

from sgd import app
from sgd.routes import BASE_MANIFEST, mask_email

TEST_USER_ID = str(uuid.uuid4())


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _fake_user_row(**overrides):
    row = {
        "id": TEST_USER_ID,
        "email": "test@example.com",
        "display_name": None,
        "active": True,
    }
    row.update(overrides)
    return row


def _patch_tenant(monkeypatch, user_row=None, gdrive=None, tmdb_api_key=None):
    """/u/<user_id>/... routes resolve their tenant via
    sgd.tenancy.load_tenant inside a before_request hook. Stub it so these
    routes can be tested without a real Postgres + Google Drive account."""
    user_row = user_row or _fake_user_row()

    def fake_load_tenant(user_id):
        g.user = user_row
        g.gdrive = gdrive
        g.tmdb_api_key = tmdb_api_key
        return user_row

    monkeypatch.setattr("sgd.tenancy.load_tenant", fake_load_tenant)
    return user_row


def test_root_redirects_to_family_login(client):
    """/ used to be a static landing page - now it sends people straight to
    the login form, since that's the actual front door for family accounts."""
    resp = client.get("/")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"


def test_health_reports_db_status(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_conn", lambda: _ok_conn_ctx())

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "db": True}


def test_health_degrades_when_db_is_unreachable(client, monkeypatch):
    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr("sgd.db.get_conn", boom)

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.get_json()["status"] == "degraded"


class _FakeConn:
    def execute(self, *a, **k):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_conn_ctx():
    return _FakeConn()


def test_user_manifest_uses_base_manifest_fields(client, monkeypatch):
    _patch_tenant(monkeypatch)

    resp = client.get(f"/u/{TEST_USER_ID}/manifest.json")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["name"] == BASE_MANIFEST["name"]
    assert body["id"] == f"{BASE_MANIFEST['id']}.{TEST_USER_ID}"


def test_user_manifest_rejects_malformed_user_id(client):
    resp = client.get("/u/not-a-uuid/manifest.json")
    assert resp.status_code == 404


def test_user_health_reports_config_and_drive(client, monkeypatch):
    class FakeAbout:
        def get(self, **kwargs):
            class R:
                def execute(self_inner):
                    return {
                        "user": {"emailAddress": "fulano@gmail.com"},
                        "storageQuota": {},
                    }
            return R()

    class FakeDriveInstance:
        def about(self):
            return FakeAbout()

    class FakeGoogleDrive:
        drive_instance = FakeDriveInstance()

    _patch_tenant(monkeypatch, gdrive=FakeGoogleDrive())
    monkeypatch.setattr("sgd.db.has_active_tmdb_key", lambda: True)

    resp = client.get(
        f"/u/{TEST_USER_ID}/health", base_url="http://addon.example.com"
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["config"] == {"tmdb": True}
    assert body["addon"]["manifest"] == f"https://addon.example.com/u/{TEST_USER_ID}/manifest.json"
    assert body["drive"]["connected"] is True
    assert body["drive"]["account"] == "f****o@gmail.com"


def test_user_health_degrades_when_drive_is_unreachable(client, monkeypatch):
    class FakeDriveInstance:
        def about(self):
            raise RuntimeError("boom")

    class FakeGoogleDrive:
        drive_instance = FakeDriveInstance()

    _patch_tenant(monkeypatch, gdrive=FakeGoogleDrive())

    resp = client.get(f"/u/{TEST_USER_ID}/health")

    assert resp.status_code == 503
    assert resp.get_json()["status"] == "degraded"


@pytest.mark.parametrize(
    "email, expected",
    [
        ("helium@gmail.com", "h****m@gmail.com"),
        ("ab@gmail.com", "a*@gmail.com"),
        ("", ""),
        (None, ""),
        ("not-an-email", "not-an-email"),
    ],
)
def test_mask_email(email, expected):
    assert mask_email(email) == expected
