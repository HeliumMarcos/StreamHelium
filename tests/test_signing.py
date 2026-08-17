import time
from types import SimpleNamespace

import pytest

from sgd import signing
from sgd.streams import Streams

ACCOUNT = "11111111-1111-1111-1111-111111111111"
VIEWER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "golden-test-secret")


def _parse(query):
    return dict(part.split("=", 1) for part in query.split("&"))


def test_signature_matches_the_worker_golden_vector(secret):
    """The Worker verifies with its own JavaScript implementation, so the
    two have to agree byte for byte. tests/cf_proxy.test.mjs asserts the
    same constant from the other side - if you change the payload format,
    change it in both."""
    digest = signing._digest(
        "golden-test-secret",
        signing._payload(ACCOUNT, "FILEID", VIEWER, "sess-abc", 4102444800),
    )
    assert digest == "6w5w36bzXv4JuDUzD3Txsx5faMHUvnp8z4mbt8jYqtI"


def test_sign_then_verify_roundtrips(secret):
    params = _parse(signing.sign(ACCOUNT, "FILEID", VIEWER, "sess-1"))

    assert signing.verify(
        ACCOUNT, "FILEID", VIEWER, "sess-1", params["e"], params["s"]
    )


def test_verify_rejects_a_different_file(secret):
    params = _parse(signing.sign(ACCOUNT, "FILEID", VIEWER, "sess-1"))

    assert not signing.verify(
        ACCOUNT, "OTHER-FILE", VIEWER, "sess-1", params["e"], params["s"]
    )


def test_verify_rejects_a_different_viewer(secret):
    """Otherwise one household member could replay another's URL and spend
    their playback slot."""
    params = _parse(signing.sign(ACCOUNT, "FILEID", VIEWER, "sess-1"))
    other = "33333333-3333-3333-3333-333333333333"

    assert not signing.verify(
        ACCOUNT, "FILEID", other, "sess-1", params["e"], params["s"]
    )


def test_verify_rejects_an_expired_url(secret):
    params = _parse(signing.sign(ACCOUNT, "FILEID", VIEWER, "sess-1", ttl_seconds=-1))

    assert not signing.verify(
        ACCOUNT, "FILEID", VIEWER, "sess-1", params["e"], params["s"]
    )


def test_verify_rejects_a_tampered_expiry(secret):
    """Pushing the expiry out has to invalidate the signature, or the
    expiry would be advisory only."""
    params = _parse(signing.sign(ACCOUNT, "FILEID", VIEWER, "sess-1"))
    later = str(int(params["e"]) + 86400)

    assert not signing.verify(ACCOUNT, "FILEID", VIEWER, "sess-1", later, params["s"])


def test_signing_is_off_without_a_secret(monkeypatch):
    monkeypatch.delenv("PROXY_SHARED_SECRET", raising=False)

    assert signing.sign(ACCOUNT, "FILEID", VIEWER, "sess-1") is None


def test_session_ids_are_unique():
    assert len({signing.new_session_id() for _ in range(100)}) == 100


# --- the URL the addon actually hands out ---------------------------------

class FakeGDrive:
    results = []
    account_id = ACCOUNT

    def get_acc_token(self):
        return "fake-access-token"


def _streams(**kwargs):
    meta = SimpleNamespace(
        type="movie", stream_type="movie", titles=["x"], year="2016",
        id="tt1", se=0, ep=0,
    )
    return Streams(FakeGDrive(), meta, **kwargs)


def test_proxy_url_is_signed_when_a_viewer_is_known(monkeypatch, secret):
    monkeypatch.setenv("CF_PROXY_URL", "https://proxy.example.com")
    monkeypatch.setattr("sgd.streams.proxy_toggle_enabled", lambda: True)

    s = _streams(viewer_id=VIEWER, session_id="sess-1")
    s.item = {"id": "FILEID", "name": "Movie.mkv"}
    s.constructed = {"behaviorHints": {}}

    url = s.get_proxy_url()
    base, _, query = url.partition("?")
    params = _parse(query)

    assert base == f"https://proxy.example.com/proxy/{ACCOUNT}/load/FILEID/Movie.mkv"
    assert params["u"] == VIEWER
    assert params["n"] == "sess-1"
    assert signing.verify(ACCOUNT, "FILEID", VIEWER, "sess-1", params["e"], params["s"])


def test_proxy_url_stays_unsigned_without_a_viewer(monkeypatch, secret):
    monkeypatch.setenv("CF_PROXY_URL", "https://proxy.example.com")
    monkeypatch.setattr("sgd.streams.proxy_toggle_enabled", lambda: True)

    s = _streams()
    s.item = {"id": "FILEID", "name": "Movie.mkv"}
    s.constructed = {"behaviorHints": {}}

    assert "?" not in s.get_proxy_url()
