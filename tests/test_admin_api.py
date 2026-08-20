"""JSON administrative API - the surface the Catálogo drives.

The point of these tests is the contract: what the Catálogo can rely on,
and what must never come out of here (password hashes, refresh tokens,
TMDB keys in the clear).
"""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from sgd import app

TOKEN = "token-de-servico-para-teste"
UID = "11111111-1111-1111-1111-111111111111"
DID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def auth(extra=None):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    headers.update(extra or {})
    return headers


def user_row(**overrides):
    """Shaped like db.list_users() really returns it: UUID and datetime
    objects, which Flask's JSON encoder does not accept on its own."""
    row = {
        "id": UUID(UID),
        "email": "familia@exemplo.com",
        "display_name": "Família",
        "active": True,
        "invite_token": "convite-abc",
        "drive_account_id": UUID(DID),
        "drive_account_pinned": False,
        "expires_at": datetime(2027, 1, 1, tzinfo=timezone.utc),
        "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "connected_at": None,
        "tmdb_connected": False,
        "has_password": True,
        "drive_label": "Drive 1",
    }
    row.update(overrides)
    return row


# --- authentication -----------------------------------------------------

def test_api_is_closed_when_no_token_is_configured(monkeypatch):
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        resp = c.get("/api/admin/ping", headers={"Authorization": "Bearer qualquer"})

    # Um ambiente ainda não configurado fica fechado, não aberto.
    assert resp.status_code == 503


def test_api_rejects_a_missing_token(client):
    assert client.get("/api/admin/ping").status_code == 401


def test_api_rejects_a_wrong_token(client):
    resp = client.get("/api/admin/ping", headers={"Authorization": "Bearer errado"})
    assert resp.status_code == 401


def test_api_rejects_the_admin_password_as_a_token(client):
    """A senha do painel é de uma pessoa digitando num formulário; a
    credencial de serviço é outra coisa, e precisa ser rotacionável sem
    trancar o admin fora do próprio painel."""
    resp = client.get("/api/admin/ping", headers={"Authorization": "Bearer test-admin-password"})
    assert resp.status_code == 401


def test_api_accepts_the_service_token(client):
    resp = client.get("/api/admin/ping", headers=auth())
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_api_responses_are_never_cached(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_users", list)
    resp = client.get("/api/admin/users", headers=auth())
    assert "no-store" in resp.headers["Cache-Control"]


def test_api_mutations_do_not_require_a_csrf_token(client, monkeypatch):
    """A proteção CSRF do painel vale para /admin, que usa cookie de
    sessão. Aqui não há cookie para uma página de terceiros abusar, e
    exigir CSRF impediria o Catálogo de chamar a API."""
    monkeypatch.setattr("sgd.db.pick_least_loaded_drive_account", lambda: None)
    monkeypatch.setattr(
        "sgd.db.create_user",
        lambda *a, **k: user_row(email="nova@exemplo.com"),
    )

    resp = client.post("/api/admin/users", json={"email": "nova@exemplo.com"}, headers=auth())
    assert resp.status_code == 201


# --- serialization ------------------------------------------------------

def test_users_are_serialized_with_plain_json_types(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_users", lambda: [user_row()])

    body = client.get("/api/admin/users", headers=auth()).get_json()
    user = body["users"][0]

    assert user["id"] == UID
    assert user["drive_account_id"] == DID
    assert user["expires_at"].startswith("2027-01-01")
    assert user["connected_at"] is None
    assert user["effectively_active"] is True


def test_expired_account_is_reported_as_not_effectively_active(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.list_users",
        lambda: [user_row(expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc))],
    )

    user = client.get("/api/admin/users", headers=auth()).get_json()["users"][0]

    # `active` sozinho mente quando a conta já venceu.
    assert user["active"] is True
    assert user["effectively_active"] is False


def test_user_payload_never_carries_credentials(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.db.list_users",
        lambda: [user_row(password_hash="scrypt:32768:8:1$segredo", google_refresh_token="1//refresh")],
    )

    raw = client.get("/api/admin/users", headers=auth()).get_data(as_text=True)

    assert "segredo" not in raw
    assert "1//refresh" not in raw
    # Saber que existe senha é útil para o painel; o hash não.
    assert client.get("/api/admin/users", headers=auth()).get_json()["users"][0]["has_password"] is True


def test_tmdb_keys_are_masked(client, monkeypatch):
    from sgd.crypto import encrypt

    monkeypatch.setattr(
        "sgd.db.list_tmdb_keys",
        lambda: [{
            "id": UUID(DID),
            "label": "Chave principal",
            "api_key": encrypt("chave-secreta-1234"),
            "active": True,
            "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        }],
    )

    resp = client.get("/api/admin/tmdb-keys", headers=auth())
    raw = resp.get_data(as_text=True)
    key = resp.get_json()["keys"][0]

    assert "chave-secreta-1234" not in raw
    assert key["masked_key"].endswith("1234")
    assert key["label"] == "Chave principal"


# --- family accounts ----------------------------------------------------

def test_create_user_rejects_an_invalid_email(client):
    resp = client.post("/api/admin/users", json={"email": "sem-arroba"}, headers=auth())

    assert resp.status_code == 400
    assert "e-mail" in resp.get_json()["error"]


def test_create_user_balances_across_the_pool_when_no_drive_is_chosen(client, monkeypatch):
    captured = {}
    monkeypatch.setattr("sgd.db.pick_least_loaded_drive_account", lambda: {"id": UUID(DID)})

    def fake_create(email, display_name=None, drive_account_id=None, expires_in_days=None, pinned=False):
        captured.update(drive_account_id=drive_account_id, pinned=pinned, days=expires_in_days)
        return user_row(email=email)

    monkeypatch.setattr("sgd.db.create_user", fake_create)

    resp = client.post(
        "/api/admin/users",
        json={"email": "nova@exemplo.com", "expires_in_days": 30},
        headers=auth(),
    )

    assert resp.status_code == 201
    assert captured["drive_account_id"] == DID
    # Sem escolha explícita a conta fica solta, para o rebalanceamento
    # poder movê-la depois.
    assert captured["pinned"] is False
    assert captured["days"] == 30


def test_create_user_pins_when_a_drive_is_chosen(client, monkeypatch):
    captured = {}

    def fake_create(email, display_name=None, drive_account_id=None, expires_in_days=None, pinned=False):
        captured.update(drive_account_id=drive_account_id, pinned=pinned)
        return user_row()

    monkeypatch.setattr("sgd.db.create_user", fake_create)

    client.post(
        "/api/admin/users",
        json={"email": "nova@exemplo.com", "drive_account_id": DID},
        headers=auth(),
    )

    assert captured["pinned"] is True


def test_create_user_reports_a_duplicate_email_as_a_conflict(client, monkeypatch):
    monkeypatch.setattr("sgd.db.pick_least_loaded_drive_account", lambda: None)

    def boom(*a, **k):
        raise Exception("duplicate key value violates unique constraint")

    monkeypatch.setattr("sgd.db.create_user", boom)

    resp = client.post("/api/admin/users", json={"email": "ja@existe.com"}, headers=auth())
    assert resp.status_code == 409


def test_unknown_user_is_a_404(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: None)

    assert client.get(f"/api/admin/users/{UID}", headers=auth()).status_code == 404
    assert client.post(f"/api/admin/users/{UID}/renew", json={}, headers=auth()).status_code == 404
    assert client.delete(f"/api/admin/users/{UID}", headers=auth()).status_code == 404


def test_setting_active_uses_the_explicit_state_not_a_toggle(client, monkeypatch):
    """O Catálogo pode estar mostrando uma lista desatualizada; um toggle
    faria o oposto do que a pessoa clicou."""
    captured = {}
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row(active=True))
    monkeypatch.setattr("sgd.db.set_active", lambda uid, active: captured.update(active=active))

    client.put(f"/api/admin/users/{UID}/active", json={"active": True}, headers=auth())

    assert captured["active"] is True


def test_omitting_the_state_falls_back_to_toggling(client, monkeypatch):
    captured = {}
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row(active=True))
    monkeypatch.setattr("sgd.db.set_active", lambda uid, active: captured.update(active=active))

    client.put(f"/api/admin/users/{UID}/active", json={}, headers=auth())

    assert captured["active"] is False


def test_renew_rejects_a_nonsense_number_of_days(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row())

    resp = client.post(f"/api/admin/users/{UID}/renew", json={"days": -5}, headers=auth())
    assert resp.status_code == 400


def test_update_user_rejects_an_invalid_date(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row())

    resp = client.patch(
        f"/api/admin/users/{UID}",
        json={"email": "ok@exemplo.com", "expires_at": "01/01/2027"},
        headers=auth(),
    )
    assert resp.status_code == 400
    assert "AAAA-MM-DD" in resp.get_json()["error"]


def test_clearing_the_expiration_ignores_any_date_sent_along(client, monkeypatch):
    captured = {}
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row())
    monkeypatch.setattr(
        "sgd.db.update_user",
        lambda uid, email, name, expires_at, clear_expiration=False: captured.update(
            expires_at=expires_at, clear=clear_expiration
        ),
    )

    client.patch(
        f"/api/admin/users/{UID}",
        json={"email": "ok@exemplo.com", "expires_at": "2027-01-01", "clear_expiration": True},
        headers=auth(),
    )

    assert captured["clear"] is True
    assert captured["expires_at"] is None


def test_reassigning_to_an_unknown_drive_is_a_404(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row())
    monkeypatch.setattr("sgd.db.get_drive_account", lambda did: None)

    resp = client.put(
        f"/api/admin/users/{UID}/drive",
        json={"drive_account_id": DID},
        headers=auth(),
    )
    assert resp.status_code == 404


def test_reassigning_drops_the_cached_drive_client(client, monkeypatch):
    """O cliente do Drive é cacheado por conta do pool enquanto a
    instância serverless está quente; sem limpar, a próxima requisição
    continuaria falando com a conta antiga."""
    from sgd.tenancy import _drive_cache

    _drive_cache[DID] = object()
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row())
    monkeypatch.setattr("sgd.db.get_drive_account", lambda did: {"id": UUID(DID), "active": True})
    monkeypatch.setattr("sgd.db.reassign_drive_account", lambda *a, **k: None)

    client.put(f"/api/admin/users/{UID}/drive", json={"drive_account_id": DID}, headers=auth())

    assert DID not in _drive_cache


def test_freeing_a_device_clears_both_locks(client, monkeypatch):
    cleared = []
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row())
    monkeypatch.setattr("sgd.db.clear_device_session", lambda uid: cleared.append("device"))
    monkeypatch.setattr("sgd.db.clear_playback_session", lambda uid: cleared.append("playback"))

    resp = client.delete(f"/api/admin/users/{UID}/device", headers=auth())

    assert resp.status_code == 200
    assert cleared == ["device", "playback"]


def test_freeing_a_device_survives_a_playback_table_error(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row())
    monkeypatch.setattr("sgd.db.clear_device_session", lambda uid: None)

    def boom(uid):
        raise Exception("relation does not exist")

    monkeypatch.setattr("sgd.db.clear_playback_session", boom)

    # Metade liberada é melhor que um erro que faz o admin achar que o
    # botão não fez nada.
    assert client.delete(f"/api/admin/users/{UID}/device", headers=auth()).status_code == 200


# --- drives and TMDB ----------------------------------------------------

def test_create_drive_requires_a_label(client):
    resp = client.post("/api/admin/drives", json={"label": "  "}, headers=auth())
    assert resp.status_code == 400


def test_deleting_a_drive_reports_how_many_families_moved(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_drive_account", lambda did: {"id": UUID(DID), "active": True})
    monkeypatch.setattr("sgd.db.redistribute_and_delete_drive_account", lambda did: 3)

    body = client.delete(f"/api/admin/drives/{DID}", headers=auth()).get_json()

    assert body["redistributed_families"] == 3


def test_create_tmdb_key_requires_label_and_key(client):
    resp = client.post("/api/admin/tmdb-keys", json={"label": "Só o nome"}, headers=auth())
    assert resp.status_code == 400


# --- settings -----------------------------------------------------------

def test_settings_report_whether_the_proxy_is_even_configured(client, monkeypatch):
    """Sem CF_PROXY_URL o interruptor é inerte, e o painel deve dizer isso
    em vez de mostrar um botão que não faz nada."""
    monkeypatch.delenv("CF_PROXY_URL", raising=False)
    monkeypatch.setattr("sgd.db.get_setting", lambda key, default=None: "1")

    proxy = client.get("/api/admin/settings", headers=auth()).get_json()["proxy"]

    assert proxy["enabled"] is True
    assert proxy["configured"] is False


def test_proxy_can_be_set_explicitly(client, monkeypatch):
    captured = {}
    monkeypatch.setattr("sgd.db.set_setting", lambda key, value: captured.update({key: value}))

    body = client.put("/api/admin/settings/proxy", json={"enabled": False}, headers=auth()).get_json()

    assert captured["cf_proxy_enabled"] == "0"
    assert body["proxy"]["enabled"] is False


# --- device and playback state ------------------------------------------

def test_device_is_reported_when_recently_seen(client, monkeypatch):
    from datetime import timedelta

    agora = datetime.now(timezone.utc)
    monkeypatch.setattr("sgd.db.list_users", lambda: [user_row(
        device_label="Nuvio 0.8.4 (Android)",
        device_last_seen=agora - timedelta(minutes=7),
    )])

    device = client.get("/api/admin/users", headers=auth()).get_json()["users"][0]["device"]

    assert device["known"] is True
    assert device["label"] == "Nuvio 0.8.4 (Android)"
    assert device["idle_minutes"] == 7
    assert device["playing"] is False


def test_a_stale_device_is_reported_as_unknown(client, monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr("sgd.db.list_users", lambda: [user_row(
        device_label="Aparelho antigo",
        # Alem do DEVICE_SESSION_TTL_MINUTES padrao (240).
        device_last_seen=datetime.now(timezone.utc) - timedelta(hours=9),
    )])

    device = client.get("/api/admin/users", headers=auth()).get_json()["users"][0]["device"]

    # A trava ja expirou: dizer que o aparelho esta conectado seria mentira.
    assert device["known"] is False
    assert device["label"] is None


def test_playback_is_reported_separately_from_the_device(client, monkeypatch):
    """Sao perguntas diferentes: `device` diz o que a pessoa usa, `playing`
    diz se tem video rodando agora. Abrir um menu e assistir um filme sao
    iguais para o primeiro sinal."""
    from datetime import timedelta

    agora = datetime.now(timezone.utc)
    monkeypatch.setattr("sgd.db.list_users", lambda: [user_row(
        device_label="Stremio (Windows)",
        device_last_seen=agora - timedelta(minutes=2),
        playback_started_at=agora - timedelta(minutes=42),
        playback_last_seen=agora - timedelta(seconds=20),
    )])

    device = client.get("/api/admin/users", headers=auth()).get_json()["users"][0]["device"]

    assert device["playing"] is True
    assert device["playing_minutes"] == 42


def test_a_paused_playback_stops_counting_as_playing(client, monkeypatch):
    from datetime import timedelta

    agora = datetime.now(timezone.utc)
    monkeypatch.setattr("sgd.db.list_users", lambda: [user_row(
        playback_started_at=agora - timedelta(minutes=50),
        # Alem do PLAYBACK_IDLE_SECONDS padrao (180s).
        playback_last_seen=agora - timedelta(minutes=6),
    )])

    device = client.get("/api/admin/users", headers=auth()).get_json()["users"][0]["device"]

    assert device["playing"] is False
    assert device["playing_since"] is None


def test_a_family_that_never_connected_reports_no_device(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_users", lambda: [user_row()])

    device = client.get("/api/admin/users", headers=auth()).get_json()["users"][0]["device"]

    assert device["known"] is False
    assert device["playing"] is False
    assert device["idle_minutes"] is None


def test_raw_session_columns_do_not_leak_into_the_payload(client, monkeypatch):
    monkeypatch.setattr("sgd.db.list_users", lambda: [user_row(
        device_label="Aparelho",
        device_last_seen=datetime.now(timezone.utc),
    )])

    user = client.get("/api/admin/users", headers=auth()).get_json()["users"][0]

    # O painel consome `device`; as colunas cruas so confundiriam.
    assert "device_last_seen" not in user
    assert "playback_last_seen" not in user


def test_a_request_without_any_header_says_that_is_normal(client):
    """Abrir a URL no navegador sempre cai aqui, e a mensagem antiga
    ("Credencial invalida") fazia parecer que o token estava errado."""
    resp = client.get("/api/admin/ping")

    assert resp.status_code == 401
    assert "não indica problema" in resp.get_json()["error"]
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")


def test_a_wrong_token_still_says_only_that_it_is_invalid(client):
    resp = client.get("/api/admin/ping", headers={"Authorization": "Bearer errado"})

    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Credencial inválida."


# --- credenciais do espectador ------------------------------------------

def _com_senha(senha="segredo123", **overrides):
    from werkzeug.security import generate_password_hash
    return user_row(password_hash=generate_password_hash(senha), **overrides)


def test_authenticate_accepts_the_right_password(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user_by_email", lambda e: _com_senha())

    resp = client.post(
        "/api/admin/authenticate",
        json={"email": "familia@exemplo.com", "password": "segredo123"},
        headers=auth(),
    )

    assert resp.status_code == 200
    assert resp.get_json()["user"]["email"] == "familia@exemplo.com"


def test_authenticate_never_returns_the_hash(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user_by_email", lambda e: _com_senha())

    bruto = client.post(
        "/api/admin/authenticate",
        json={"email": "familia@exemplo.com", "password": "segredo123"},
        headers=auth(),
    ).get_data(as_text=True)

    assert "scrypt" not in bruto and "pbkdf2" not in bruto
    assert "password_hash" not in bruto


def test_authenticate_rejects_the_wrong_password(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user_by_email", lambda e: _com_senha())

    resp = client.post(
        "/api/admin/authenticate",
        json={"email": "familia@exemplo.com", "password": "errada"},
        headers=auth(),
    )

    assert resp.status_code == 401


def test_an_unknown_email_looks_the_same_as_a_wrong_password(client, monkeypatch):
    """Respostas diferentes revelariam quais e-mails existem."""
    monkeypatch.setattr("sgd.db.get_user_by_email", lambda e: None)

    resp = client.post(
        "/api/admin/authenticate",
        json={"email": "ninguem@exemplo.com", "password": "qualquer"},
        headers=auth(),
    )

    assert resp.status_code == 401
    assert resp.get_json()["error"] == "E-mail ou senha incorretos."


def test_an_account_without_a_password_cannot_log_in(client, monkeypatch):
    # Convidada mas ainda sem senha definida.
    monkeypatch.setattr("sgd.db.get_user_by_email", lambda e: user_row(password_hash=None))

    resp = client.post(
        "/api/admin/authenticate",
        json={"email": "familia@exemplo.com", "password": ""},
        headers=auth(),
    )

    assert resp.status_code == 401


def test_a_disabled_account_is_told_so_instead_of_wrong_password(client, monkeypatch):
    """Dizer "senha incorreta" para quem digitou certo manda a pessoa
    tentar de novo para sempre."""
    monkeypatch.setattr("sgd.db.get_user_by_email", lambda e: _com_senha(active=False))

    resp = client.post(
        "/api/admin/authenticate",
        json={"email": "familia@exemplo.com", "password": "segredo123"},
        headers=auth(),
    )

    assert resp.status_code == 403
    assert "desativado" in resp.get_json()["error"]


def test_an_expired_account_is_told_it_expired(client, monkeypatch):
    from datetime import timedelta
    monkeypatch.setattr(
        "sgd.db.get_user_by_email",
        lambda e: _com_senha(expires_at=datetime.now(timezone.utc) - timedelta(days=1)),
    )

    resp = client.post(
        "/api/admin/authenticate",
        json={"email": "familia@exemplo.com", "password": "segredo123"},
        headers=auth(),
    )

    assert resp.status_code == 403
    assert "expirou" in resp.get_json()["error"]


def test_an_invite_resolves_to_its_account(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user_by_invite_token", lambda t: user_row())

    resp = client.get("/api/admin/invites/convite-abc", headers=auth())

    assert resp.status_code == 200
    assert resp.get_json()["user"]["display_name"] == "Família"


def test_an_unknown_invite_is_a_404(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user_by_invite_token", lambda t: None)

    assert client.get("/api/admin/invites/nao-existe", headers=auth()).status_code == 404


def test_an_invite_for_an_expired_account_does_not_open(client, monkeypatch):
    from datetime import timedelta
    monkeypatch.setattr(
        "sgd.db.get_user_by_invite_token",
        lambda t: user_row(expires_at=datetime.now(timezone.utc) - timedelta(days=1)),
    )

    # Definir senha ali criaria a expectativa de um acesso que nao funciona.
    assert client.get("/api/admin/invites/convite-abc", headers=auth()).status_code == 403


def test_setting_a_password_stores_a_hash_not_the_password(client, monkeypatch):
    guardado = {}
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row())
    monkeypatch.setattr("sgd.db.set_password", lambda uid, h: guardado.update(hash=h))

    resp = client.put(
        f"/api/admin/users/{UID}/password",
        json={"password": "senhanova123"},
        headers=auth(),
    )

    assert resp.status_code == 200
    assert "senhanova123" not in guardado["hash"]
    assert len(guardado["hash"]) > 40


def test_a_short_password_is_refused(client, monkeypatch):
    monkeypatch.setattr("sgd.db.get_user", lambda uid: user_row())

    resp = client.put(
        f"/api/admin/users/{UID}/password",
        json={"password": "12345"},
        headers=auth(),
    )

    assert resp.status_code == 400
    assert "6 caracteres" in resp.get_json()["error"]


def test_the_credential_endpoints_need_the_service_token(client):
    assert client.post("/api/admin/authenticate", json={}).status_code == 401
    assert client.get("/api/admin/invites/x").status_code == 401
    assert client.put(f"/api/admin/users/{UID}/password", json={}).status_code == 401
