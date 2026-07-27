import pytest

from sgd import app
from sgd.routes import MANIFEST, mask_email


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_home_renders_addon_details(client):
    resp = client.get("/", base_url="http://addon.example.com")

    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)

    assert MANIFEST["name"] in body
    assert MANIFEST["version"] in body
    assert MANIFEST["logo"] in body
    # The install links must point at this deployment, not a hardcoded host.
    assert "https://addon.example.com/manifest.json" in body
    assert "stremio://addon.example.com/manifest.json" in body
    # No unsubstituted template placeholders left over.
    assert "$" not in body


def test_home_is_not_indexed(client):
    resp = client.get("/")
    assert resp.headers["X-Robots-Tag"] == "noindex"


def test_health_reports_config_and_drive(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.routes.drive_status",
        lambda: {"connected": True, "account": "f****e@gmail.com"},
    )
    monkeypatch.setenv("TMDB_API_KEY", "key")
    monkeypatch.delenv("CF_PROXY_URL", raising=False)

    resp = client.get("/health", base_url="http://addon.example.com")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["config"] == {"tmdb": True, "cf_proxy": False}
    assert body["addon"]["manifest"] == "https://addon.example.com/manifest.json"
    assert body["drive"]["connected"] is True


def test_health_degrades_when_drive_is_unreachable(client, monkeypatch):
    monkeypatch.setattr(
        "sgd.routes.drive_status",
        lambda: {"connected": False, "error": "HttpError"},
    )

    resp = client.get("/health")

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
