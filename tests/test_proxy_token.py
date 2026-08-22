from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from sgd import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class FakeDrive:
    def __init__(self, token="an-access-token", expires_in=timedelta(minutes=45)):
        self._token = token
        self.acc_token = SimpleNamespace(
            contents={"expires_in": datetime.now() + expires_in}
        )

    def get_acc_token(self):
        return self._token


ACCOUNT = "11111111-1111-1111-1111-111111111111"


def _auth(secret="shared-secret"):
    return {"Authorization": f"Bearer {secret}"}


def test_disabled_when_no_shared_secret_is_configured(client, monkeypatch):
    monkeypatch.delenv("PROXY_SHARED_SECRET", raising=False)
    resp = client.get(f"/internal/drive-token/{ACCOUNT}", headers=_auth())
    assert resp.status_code == 503


def test_rejects_wrong_secret(client, monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")
    resp = client.get(f"/internal/drive-token/{ACCOUNT}", headers=_auth("wrong"))
    assert resp.status_code == 401


def test_rejects_missing_authorization_header(client, monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")
    resp = client.get(f"/internal/drive-token/{ACCOUNT}")
    assert resp.status_code == 401


def test_returns_token_and_remaining_lifetime(client, monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")
    monkeypatch.setattr("sgd.tenancy.drive_for_account", lambda _: FakeDrive())

    resp = client.get(f"/internal/drive-token/{ACCOUNT}", headers=_auth())

    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    body = resp.get_json()
    assert body["access_token"] == "an-access-token"
    # ~45 minutes, allowing for the clock moving during the test.
    assert 2600 < body["expires_in"] <= 2700


def test_never_reports_less_than_the_floor(client, monkeypatch):
    """An almost-expired (or unparseable) token still reports a usable
    lifetime, otherwise the Worker would re-fetch on every single request."""
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")
    drive = FakeDrive(expires_in=timedelta(seconds=3))
    monkeypatch.setattr("sgd.tenancy.drive_for_account", lambda _: drive)

    resp = client.get(f"/internal/drive-token/{ACCOUNT}", headers=_auth())

    assert resp.get_json()["expires_in"] == 60


def test_unknown_account_is_404(client, monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")
    monkeypatch.setattr("sgd.tenancy.drive_for_account", lambda _: None)

    resp = client.get(f"/internal/drive-token/{ACCOUNT}", headers=_auth())

    assert resp.status_code == 404


def test_malformed_account_id_never_reaches_the_database(client, monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")

    def boom(_):
        raise AssertionError("should not have been called")

    monkeypatch.setattr("sgd.tenancy.drive_for_account", boom)

    resp = client.get("/internal/drive-token/not-a-uuid", headers=_auth())

    assert resp.status_code == 404


def test_failed_refresh_is_502(client, monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")
    drive = FakeDrive(token=None)
    monkeypatch.setattr("sgd.tenancy.drive_for_account", lambda _: drive)

    resp = client.get(f"/internal/drive-token/{ACCOUNT}", headers=_auth())

    assert resp.status_code == 502


# --- playback slot ---------------------------------------------------------

def test_playback_claim_grants_and_reports_renewal(client, monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")
    monkeypatch.setattr("sgd.db.claim_playback_session", lambda *a: True)

    resp = client.post(
        f"/internal/playback/{ACCOUNT}", headers=_auth(), json={"session": "sess-1"}
    )

    assert resp.status_code == 200
    assert resp.get_json()["granted"] is True
    assert resp.get_json()["renew_after"] > 0


def test_another_session_is_never_refused(client, monkeypatch):
    """A vaga de reproducao deixou de bloquear.

    Um `session_id` novo nasce a cada listagem de streams, mas a vaga so
    era liberada apos tres minutos de silencio - entao abrir o proximo
    episodio, ou o player reabrir a lista no meio do filme, fazia a pessoa
    competir consigo mesma. Vinha 409, o Worker traduzia em 403 e o player
    travava.
    """
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")
    # Mesmo que a camada de banco volte a recusar, o endpoint nao pode
    # transformar isso num 409: e o 409 que o Worker traduz em 403, e e o
    # 403 que trava o player.
    monkeypatch.setattr("sgd.db.claim_playback_session", lambda *a, **k: False)

    resp = client.post(
        f"/internal/playback/{ACCOUNT}", headers=_auth(), json={"session": "sess-2"}
    )

    assert resp.status_code == 200
    assert resp.get_json()["granted"] is True


def test_playback_claim_needs_a_session(client, monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")

    resp = client.post(f"/internal/playback/{ACCOUNT}", headers=_auth(), json={})

    assert resp.status_code == 400


def test_playback_claim_rejects_wrong_secret(client, monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")

    resp = client.post(
        f"/internal/playback/{ACCOUNT}",
        headers=_auth("wrong"),
        json={"session": "sess-1"},
    )

    assert resp.status_code == 401


def test_playback_claim_fails_open_on_db_error(client, monkeypatch):
    """The limit is a convenience. A database hiccup must not stop the
    household from watching anything."""
    monkeypatch.setenv("PROXY_SHARED_SECRET", "shared-secret")

    def boom(*a):
        raise RuntimeError("db down")

    monkeypatch.setattr("sgd.db.claim_playback_session", boom)

    resp = client.post(
        f"/internal/playback/{ACCOUNT}", headers=_auth(), json={"session": "sess-1"}
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"granted": True, "degraded": True}
