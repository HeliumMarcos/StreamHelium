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


# --- eventos de operação -------------------------------------------------

def test_recording_an_event_never_raises(monkeypatch):
    """Registrar que algo deu errado não pode ser mais uma coisa dando errado.

    Todos os pontos que chamam isto já estão num caminho degradado — se o
    registro estourasse, derrubaria a reprodução que ainda ia funcionar.
    """
    def boom(*a, **k):
        raise RuntimeError("banco fora")

    monkeypatch.setattr("sgd.db.get_conn", boom)

    db.record_event("catalogo_recusou", "HTTP 406")  # não levanta


def test_pruning_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("banco fora")

    monkeypatch.setattr("sgd.db.get_conn", boom)

    db.forget_events_older_than(60)  # não levanta
