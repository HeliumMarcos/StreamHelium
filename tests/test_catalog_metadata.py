"""Metadata coming from the Catálogo instead of the open internet.

Two things matter here and neither is speed: that a catalogued title
produces the same shape as the old path, and that a broken Catálogo is
never worse than not having asked.
"""

import pytest

from sgd import catalog

IMDB_ID = "tt0137523"


class FakeResponse:
    def __init__(self, status_code, payload=None, body=None):
        self.status_code = status_code
        self._payload = payload
        self._body = body

    def json(self):
        if self._body is not None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("CATALOG_API_URL", "https://catalogo.exemplo")
    monkeypatch.setenv("ADMIN_API_TOKEN", "segredo-compartilhado")


def fake_get(monkeypatch, response, capture=None):
    def _get(url, headers=None, timeout=None):
        if capture is not None:
            capture.update(url=url, headers=headers or {}, timeout=timeout)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("sgd.catalog.requests.get", _get)


# --- o cliente ----------------------------------------------------------

def test_a_catalogued_title_comes_back(configured, monkeypatch):
    fake_get(monkeypatch, FakeResponse(200, {
        "imdb_id": IMDB_ID,
        "type": "movie",
        "name": "Clube da Luta",
        "original_name": "Fight Club",
        "year": "1999",
    }))

    data = catalog.lookup(IMDB_ID)

    assert data["name"] == "Clube da Luta"
    assert data["original_name"] == "Fight Club"


def test_the_shared_secret_goes_as_a_bearer_header(configured, monkeypatch):
    capturado = {}
    fake_get(monkeypatch, FakeResponse(200, {"name": "X"}), capturado)

    catalog.lookup(IMDB_ID)

    assert capturado["headers"]["Authorization"] == "Bearer segredo-compartilhado"
    assert capturado["url"] == f"https://catalogo.exemplo/api/interno/titulos/{IMDB_ID}"


def test_the_timeout_is_short(configured, monkeypatch):
    """Isto roda na frente do caminho antigo: no pior caso e latencia
    somada em todo play. Desistir rapido e melhor que esperar duas vezes."""
    capturado = {}
    fake_get(monkeypatch, FakeResponse(200, {"name": "X"}), capturado)

    catalog.lookup(IMDB_ID)

    assert capturado["timeout"] <= 3


def test_a_title_outside_the_catalogue_is_not_an_error(configured, monkeypatch):
    fake_get(monkeypatch, FakeResponse(404))

    assert catalog.lookup(IMDB_ID) is None


def test_an_unreachable_catalogue_falls_through(configured, monkeypatch):
    fake_get(monkeypatch, ConnectionError("sem rede"))

    # Nunca pior que nao ter perguntado.
    assert catalog.lookup(IMDB_ID) is None


def test_a_server_error_falls_through(configured, monkeypatch):
    fake_get(monkeypatch, FakeResponse(500))

    assert catalog.lookup(IMDB_ID) is None


def test_a_non_json_body_falls_through(configured, monkeypatch):
    fake_get(monkeypatch, FakeResponse(200, body="<html>erro</html>"))

    assert catalog.lookup(IMDB_ID) is None


def test_a_record_without_any_title_is_treated_as_absent(configured, monkeypatch):
    """Deixar passar significaria buscar string vazia no Drive."""
    fake_get(monkeypatch, FakeResponse(200, {"imdb_id": IMDB_ID, "year": "1999"}))

    assert catalog.lookup(IMDB_ID) is None


def test_nothing_is_requested_without_the_shared_secret(monkeypatch):
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    chamou = []
    monkeypatch.setattr("sgd.catalog.requests.get", lambda *a, **k: chamou.append(1))

    assert catalog.lookup(IMDB_ID) is None
    assert chamou == []


# --- integração com o Meta ----------------------------------------------

def test_catalogue_titles_are_sanitised_like_every_other_source(monkeypatch):
    """O Catalogo devolve titulos crus; quem gera as variantes e o mesmo
    sanitize aplicado aos resultados do TMDB. Duas normalizacoes
    divergiriam, e a divergencia apareceria como filme sumido da busca."""
    from sgd.meta import IMDb

    monkeypatch.setattr("sgd.catalog.lookup", lambda _id: {
        "name": "O Senhor dos Anéis: A Sociedade do Anel",
        "original_name": "The Lord of the Rings: The Fellowship of the Ring",
        "year": "2001",
    })

    meta = IMDb.__new__(IMDb)
    meta.titles = []
    meta.name = None
    meta.original_title = None
    meta.year = None
    meta.id = "tt0120737"

    assert meta.get_meta_from_catalog() is True

    # Dois-pontos viram espaco, tudo minusculo - igual ao caminho do TMDB.
    assert "o senhor dos aneis a sociedade do anel" in meta.titles or \
           "o senhor dos anéis a sociedade do anel" in meta.titles
    assert "the lord of the rings the fellowship of the ring" in meta.titles
    assert meta.year == "2001"
    assert meta.name.startswith("O Senhor dos An")


def test_a_title_outside_the_catalogue_leaves_the_old_path_untouched(monkeypatch):
    from sgd.meta import IMDb

    monkeypatch.setattr("sgd.catalog.lookup", lambda _id: None)

    meta = IMDb.__new__(IMDb)
    meta.titles = []
    meta.name = None
    meta.original_title = None
    meta.year = None
    meta.id = IMDB_ID

    assert meta.get_meta_from_catalog() is False
    assert meta.titles == []


def test_a_nonsense_year_from_the_catalogue_is_ignored(monkeypatch):
    """O ano e o que descarta um arquivo de outro filme de mesmo nome;
    aceitar lixo ali seria pior que nao ter ano nenhum."""
    from sgd.meta import IMDb

    monkeypatch.setattr("sgd.catalog.lookup", lambda _id: {
        "name": "Filme",
        "year": "em breve",
    })

    meta = IMDb.__new__(IMDb)
    meta.titles = []
    meta.name = None
    meta.original_title = None
    meta.year = None
    meta.id = IMDB_ID

    meta.get_meta_from_catalog()

    assert meta.year is None
