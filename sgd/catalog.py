"""Metadata from the Catálogo, before falling back to the open internet.

Every playback request has to resolve a title, an original title and a
year before it can search the Drive - that is what decides whether a file
is found at all. Until now that meant TMDB, then Cinemeta, then scraping
the IMDb release-info page, with a /tmp cache that does not survive
between invocations on Vercel.

But the Catálogo's MySQL already holds those fields, curated by hand, for
every title the family can watch - and the person who curated them is the
same one who named the files in the Drive, which makes them the titles
most likely to actually match.

What this removes is the IMDb half: the suggestions API and the HTML
scrape of the release-info page, the slowest sources and the one that
breaks whenever the page changes. TMDB and Cinemeta keep running, because
each adds title variants the catalogue does not have, and every extra
variant is another chance of matching how a file was really named.

Anything not catalogued falls through to the old path, untouched.

Fails soft on purpose: a slow or unreachable Catálogo must never be worse
than not having asked. Any error returns None and the old path runs.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

# Short: this sits in front of the old path, so its worst case is added
# latency on every play. Better to give up quickly and ask TMDB than to
# make the viewer wait twice.
TIMEOUT_SECONDS = float(os.environ.get("CATALOG_API_TIMEOUT", "2.5"))

# O mod_security da HostGator devolve 406 para o User-Agent padrao do
# `requests` ("python-requests/2.32.3"). Sem isto, TODA consulta ao
# Catalogo era recusada antes de chegar ao Laravel, e o addon caia
# silenciosamente para TMDB e Cinemeta - perdendo exatamente os titulos
# curados que casam com o nome dos arquivos no Drive.
#
# O sintoma nao parecia um bloqueio: aparecia como filme sem nenhuma opcao
# de stream, so nos titulos cujo arquivo foi nomeado pelo nome curado.
#
# Qualquer identificacao sem "python-requests" passa. Esta diz quem somos,
# que e o que um User-Agent deveria fazer.
USER_AGENT = "StreamHelium/1.0 (+https://stream-helium.vercel.app)"


def _base_url() -> str | None:
    url = os.environ.get("CATALOG_API_URL", "https://catalogo.heliummarcos.com.br")
    return url.rstrip("/") or None


def _token() -> str | None:
    """O segredo com que ESTE app chama o Catalogo.

    Ja foi o mesmo dos dois sentidos, defendido com o argumento de que os
    dois sistemas ja confiam um no outro. O argumento estava errado: o que
    difere nao e a confianca, e o raio de dano. Este token vive no .env de
    uma hospedagem compartilhada e serve so para consultar titulos; o
    ADMIN_API_TOKEN abre a API administrativa inteira. Com um valor so,
    vazar o primeiro entregava o segundo.

    Aceita o antigo enquanto CATALOG_API_TOKEN nao estiver definido, para
    a separacao poder ser publicada antes de a variavel existir. Quando o
    novo estiver no ar dos dois lados, remova ADMIN_API_TOKEN daqui.
    """
    novo = os.environ.get("CATALOG_API_TOKEN")
    if novo:
        return novo

    antigo = os.environ.get("ADMIN_API_TOKEN")
    if antigo:
        logger.warning(
            "Chamando o Catalogo com ADMIN_API_TOKEN. Defina CATALOG_API_TOKEN "
            "e o mesmo valor como CATALOG_API_TOKEN no Catalogo."
        )
    return antigo


def lookup(imdb_id: str) -> dict | None:
    """Curated metadata for an IMDb id, or None to use the old path.

    Returns the raw titles. Generating the search variants stays with
    meta.py's own `sanitize`, the same one applied to TMDB results - two
    implementations of normalisation would drift, and the drift would show
    up as "this film disappeared from search".
    """
    base = _base_url()
    token = _token()
    if not base or not token or not imdb_id:
        return None

    try:
        response = requests.get(
            f"{base}/api/interno/titulos/{imdb_id}",
            headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.info("Catalogue lookup for %s failed, using the usual sources: %s", imdb_id, e)
        return None

    if response.status_code == 404:
        # Not catalogued. Expected and common - not worth a warning.
        return None

    if response.status_code != 200:
        logger.warning(
            "Catalogue answered %s for %s; using the usual sources.",
            response.status_code, imdb_id,
        )
        return None

    try:
        data = response.json()
    except ValueError:
        logger.warning("Catalogue returned a non-JSON body for %s.", imdb_id)
        return None

    if not isinstance(data, dict) or not (data.get("name") or data.get("original_name")):
        # A record with no usable title is the same as no record: letting
        # it through would mean searching the Drive for an empty string.
        return None

    return data
