import pytest

from sgd.utils import split_stream_id
from sgd.routes import is_valid_stream_id


@pytest.mark.parametrize("stream_id", ["tt1234567:1:2", "tt1234567%3A1%3A2"])
def test_split_stream_id_handles_both_colon_encodings(stream_id):
    assert split_stream_id(stream_id) == ["tt1234567", "1", "2"]


def test_split_stream_id_no_separator():
    assert split_stream_id("tt1234567") == ["tt1234567"]


@pytest.mark.parametrize(
    "stream_id",
    [
        "tt1234567",
        "tt1234567:1:2",
        "tt1234567%3A1%3A2",
        "tmdb:12345",
        "tmdb%3A12345",
        "tmdb:12345:1:2",
        "TT1234567",
    ],
)
def test_valid_stream_ids(stream_id):
    assert is_valid_stream_id(stream_id)


@pytest.mark.parametrize(
    "stream_id",
    [
        "garbage",
        "tt12",
        "tmdb",
        "tmdb:notanumber",
        "tt1234567:abc:2",
        "tt1234567:1:2:3",
    ],
)
def test_invalid_stream_ids(stream_id):
    assert not is_valid_stream_id(stream_id)


# --- registro de abertura de titulo -------------------------------------

def test_a_series_episode_is_recorded_as_its_title(monkeypatch):
    """tt1234567:2:5 e o mesmo titulo que tt1234567:1:1 — o catalogo
    raciocina em titulos, nao em episodios."""
    from sgd.routes import _record_view

    registrado = []
    monkeypatch.setattr("sgd.db.record_title_view", lambda u, i: registrado.append((u, i)))

    _record_view("user-1", "tt0903747:2:5")

    assert registrado == [("user-1", "tt0903747")]


def test_a_tmdb_id_is_not_recorded(monkeypatch):
    """O acervo do Catalogo e todo indexado por IMDb: um id tmdb nunca
    casaria com nada la, e guardar so criaria linhas mortas."""
    from sgd.routes import _record_view

    registrado = []
    monkeypatch.setattr("sgd.db.record_title_view", lambda u, i: registrado.append((u, i)))

    _record_view("user-1", "tmdb:550")

    assert registrado == []


def test_recording_a_view_never_breaks_playback(monkeypatch):
    """Contabilizar uma visualizacao nao vale interromper uma reproducao."""
    import sgd.db as db

    def explode(*a, **k):
        raise Exception("banco fora do ar")

    monkeypatch.setattr("sgd.db.get_conn", explode)

    # Nao levanta.
    db.record_title_view("user-1", "tt0137523")


# --- a raiz: Meta não pode sujar `ep` ------------------------------------

def _parse_series_id(stream_id):
    """Só o trecho de Meta.__init__ que interpreta o id, sem rede nem cache."""
    from sgd import utils as ut

    ep, se = 0, 0
    partes = ut.split_stream_id(stream_id)
    if len(partes) >= 3 and str(partes[-1]).isdigit() and str(partes[-2]).isdigit():
        ep = str(partes[-1]).zfill(2)
        se = str(partes[-2]).zfill(2)
    return se, ep


def test_a_series_id_without_a_suffix_leaves_season_and_episode_alone():
    """O defeito era `ep` receber o próprio id IMDb.

    A atribuição de `ep` vinha antes de `se` estourar IndexError, e o
    except engolia — sobrando ep="tt27497393". O int() disso estourava lá
    na frente, no meio de uma resposta já iniciada.
    """
    se, ep = _parse_series_id("tt27497393")

    assert (se, ep) == (0, 0)


def test_a_complete_series_id_still_parses():
    assert _parse_series_id("tt0903747:4:8") == ("04", "08")
    assert _parse_series_id("tt0903747%3A4%3A8") == ("04", "08")


def test_a_malformed_suffix_is_ignored_rather_than_trusted():
    # Melhor não buscar do que buscar por "int('x')".
    assert _parse_series_id("tt0903747:temporada:8") == (0, 0)


# --- convite para pedir --------------------------------------------------

def test_a_title_with_no_file_offers_a_way_to_ask_for_it(monkeypatch):
    """Uma lista vazia deixa a pessoa sem saída.

    O Stremio só mostra "nenhum stream" e o assunto morre ali. Um item
    único, que leva ao pedido, transforma o beco sem saída numa ação — e
    avisa o administrador do que falta, que é a informação mais difícil de
    obter de outro jeito.
    """
    monkeypatch.setenv("CATALOG_API_URL", "https://catalogo.exemplo")

    from sgd.routes import _convite_para_pedir

    item = _convite_para_pedir("Duna Parte Dois")

    # externalUrl e não url: o Stremio abre no navegador em vez de tentar
    # tocar.
    assert "externalUrl" in item
    assert "url" not in item
    assert item["externalUrl"].startswith("https://catalogo.exemplo/minha-conta?pedir=")
    assert "Duna%20Parte%20Dois" in item["externalUrl"]


def test_the_invitation_survives_a_title_with_odd_characters():
    from sgd.routes import _convite_para_pedir

    item = _convite_para_pedir("Amélie & Cia / 2001")

    # Um título mal escapado quebraria o link em silêncio.
    assert " " not in item["externalUrl"]
    assert "&" not in item["externalUrl"].split("?", 1)[1].replace("pedir=", "")
