import uuid
from datetime import datetime, timedelta, timezone

import pytest
from flask import g
from google.auth.exceptions import RefreshError

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


def test_expired_user_landing_explains_status_instead_of_404(client, monkeypatch):
    expired_user = _fake_user_row(
        expires_at=datetime.now(timezone.utc) - timedelta(days=1)
    )

    def fake_load_user_landing(user_id):
        g.user = expired_user
        g.gdrive = None
        return expired_user

    monkeypatch.setattr("sgd.tenancy.load_user_landing", fake_load_user_landing)
    monkeypatch.setattr("sgd.db.has_active_tmdb_key", lambda: True)

    resp = client.get(f"/u/{TEST_USER_ID}/")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Instalação temporariamente indisponível" in body
    assert "período de acesso desta conta terminou" in body
    assert "stremio://" not in body


def test_active_user_landing_has_semantic_document_and_correct_routes(client, monkeypatch):
    active_user = _fake_user_row(expires_at=None)

    def fake_load_user_landing(user_id):
        g.user = active_user
        g.gdrive = object()
        return active_user

    monkeypatch.setattr("sgd.tenancy.load_user_landing", fake_load_user_landing)
    monkeypatch.setattr("sgd.db.has_active_tmdb_key", lambda: True)

    body = client.get(f"/u/{TEST_USER_ID}/").get_data(as_text=True)

    assert "<!doctype html>" in body.lower()
    assert '<html lang="pt-BR">' in body
    assert '<main id="conteudo">' in body
    assert "Rotas utilizadas por esta conta" in body
    assert "/u/&lt;seu-id&gt;/manifest.json" in body


def test_user_health_reports_config_and_drive(client, monkeypatch):
    class FakeAbout:
        def get(self, **kwargs):
            class R:
                def execute(self_inner, **kwargs):
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
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert body["drive"]["error_code"] == "temporarily_unavailable"
    assert body["drive"]["reconnect_required"] is False


def test_user_health_explains_expired_google_authorization(client, monkeypatch):
    class FakeRequest:
        def execute(self, **kwargs):
            raise RefreshError(
                "invalid_grant: Token has been expired or revoked.",
                {
                    "error": "invalid_grant",
                    "error_description": "Token has been expired or revoked.",
                },
            )

    class FakeAbout:
        def get(self, **kwargs):
            return FakeRequest()

    class FakeDriveInstance:
        def about(self):
            return FakeAbout()

    class FakeGoogleDrive:
        drive_instance = FakeDriveInstance()

    _patch_tenant(monkeypatch, gdrive=FakeGoogleDrive())

    resp = client.get(f"/u/{TEST_USER_ID}/health")

    assert resp.status_code == 503
    drive = resp.get_json()["drive"]
    assert drive["error_code"] == "authorization_expired"
    assert drive["reconnect_required"] is True
    assert "expirada ou revogada" in drive["error"]


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
