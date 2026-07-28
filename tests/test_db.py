from datetime import datetime, timedelta, timezone

from sgd import db


def test_active_no_expiration_is_effectively_active():
    user = {"active": True, "expires_at": None}
    assert db.is_effectively_active(user) is True


def test_active_future_expiration_is_effectively_active():
    user = {
        "active": True,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
    }
    assert db.is_effectively_active(user) is True


def test_active_past_expiration_is_not_effectively_active():
    user = {
        "active": True,
        "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
    }
    assert db.is_effectively_active(user) is False


def test_inactive_is_never_effectively_active_regardless_of_expiration():
    user = {
        "active": False,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
    }
    assert db.is_effectively_active(user) is False
