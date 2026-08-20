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


def _base_url() -> str | None:
    url = os.environ.get("CATALOG_API_URL", "https://catalogo.heliummarcos.com.br")
    return url.rstrip("/") or None


def _token() -> str | None:
    """The same shared secret the Catálogo uses to call this app, valid in
    both directions. Two secrets between two systems that already trust
    each other would add a variable to misconfigure, not isolation."""
    return os.environ.get("ADMIN_API_TOKEN")


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
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
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
