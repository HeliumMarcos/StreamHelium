from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from sgd import app
from sgd.family_auth import build_account_status

USER_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _user_row(**overrides):
    row = {
        "id": USER_ID,
        "email": "familia@example.com",
        "active": True,
        "expires_at": None,
        "password_hash": generate_password_hash("segredo123"),
    }
    row.update(overrides)
    return row


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Entrar" in resp.get_data(as_text=True)


def test_login_wrong_password(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user_by_email", lambda email: _user_row())

    resp = client.post(
        "/login", data={"email": "familia@example.com", "password": "errada"}
    )

    assert resp.status_code == 200
    assert "incorretos" in resp.get_data(as_text=True)


def test_login_unknown_email(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user_by_email", lambda email: None)

    resp = client.post(
        "/login", data={"email": "ninguem@example.com", "password": "x"}
    )

    assert resp.status_code == 200
    assert "incorretos" in resp.get_data(as_text=True)


def test_login_correct_password_sets_session(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user_by_email", lambda email: _user_row())

    resp = client.post(
        "/login", data={"email": "familia@example.com", "password": "segredo123"}
    )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/home"
    with client.session_transaction() as sess:
        assert sess["family_user_id"] == USER_ID


def test_login_correct_password_but_expired(client, monkeypatch):
    expired_row = _user_row(
        expires_at=datetime.now(timezone.utc) - timedelta(days=1)
    )
    monkeypatch.setattr("sgd.db.get_user_by_email", lambda email: expired_row)

    resp = client.post(
        "/login", data={"email": "familia@example.com", "password": "segredo123"}
    )

    assert resp.status_code == 200
    assert "inativa ou expirada" in resp.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert "family_user_id" not in sess


def test_logout_clears_session(client):
    with client.session_transaction() as sess:
        sess["family_user_id"] = USER_ID

    resp = client.post("/logout")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"
    with client.session_transaction() as sess:
        assert "family_user_id" not in sess


def test_home_without_session_redirects_to_login(client):
    resp = client.get("/home")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"


def test_home_with_valid_session_redirects_to_addon_page(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: _user_row())
    with client.session_transaction() as sess:
        sess["family_user_id"] = USER_ID

    resp = client.get("/home")

    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/u/{USER_ID}/"


def test_home_with_stale_session_clears_and_redirects(client, monkeypatch):
    """Account was deleted/deactivated after the session cookie was issued."""
    monkeypatch.setattr("sgd.db.get_user", lambda uid: None)
    with client.session_transaction() as sess:
        sess["family_user_id"] = USER_ID

    resp = client.get("/home")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"
    with client.session_transaction() as sess:
        assert "family_user_id" not in sess


def test_set_password_mismatch_redirects_with_error(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.get_user_by_invite_token", lambda token: _user_row(password_hash=None)
    )
    resp = client.post(
        "/connect/tok123/set-password",
        data={"password": "abcdef", "confirm": "different"},
    )
    assert resp.status_code == 302
    assert "password_error=1" in resp.headers["Location"]


def test_set_password_too_short_redirects_with_error(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.get_user_by_invite_token", lambda token: _user_row(password_hash=None)
    )
    resp = client.post(
        "/connect/tok123/set-password", data={"password": "abc", "confirm": "abc"}
    )
    assert resp.status_code == 302
    assert "password_error=1" in resp.headers["Location"]


def test_set_password_success_calls_db(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.get_user_by_invite_token", lambda token: _user_row(password_hash=None)
    )
    calls = []
    monkeypatch.setattr(
        "sgd.db.set_password", lambda uid, pw_hash: calls.append((uid, pw_hash))
    )

    resp = client.post(
        "/connect/tok123/set-password",
        data={"password": "abcdef", "confirm": "abcdef"},
    )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/connect/tok123"
    assert len(calls) == 1
    assert calls[0][0] == USER_ID


# --- build_account_status (pure function) ---------------------------------

def test_account_status_inactive():
    assert build_account_status({"active": False}) == {"active": False}


def test_account_status_no_expiration():
    assert build_account_status({"active": True, "expires_at": None}) == {
        "active": True, "days_left": None
    }


def test_account_status_expired():
    row = {"active": True, "expires_at": datetime.now(timezone.utc) - timedelta(days=1)}
    result = build_account_status(row)
    assert result == {"active": True, "expired": True}


def test_account_status_days_left():
    row = {"active": True, "expires_at": datetime.now(timezone.utc) + timedelta(days=5, hours=1)}
    result = build_account_status(row)
    assert result["active"] is True
    assert result["days_left"] == 5
