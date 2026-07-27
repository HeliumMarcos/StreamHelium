import json
import logging
import unicodedata
import requests
from sgd.cache import Pickle

logger = logging.getLogger(__name__)

# Shared stop-word list used both when building Google Drive search queries
# (sgd/gdrive.py) and when validating candidate title matches (sgd/streams.py).
STOP_WORDS = {
    "the", "of", "and", "a", "an", "to", "in", "for", "on", "at",
    "by", "with", "from", "as", "is", "it",
    "o", "os", "as", "um", "uma", "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas", "por", "para", "com", "se", "que", "ou",
}


def split_stream_id(stream_id):
    """Split a Stremio stream id ("tt1234567:1:2") into its parts.

    Stremio encodes the ':' separator as "%3A" in the request URL. Depending
    on the WSGI server/adapter, that may reach us either still literally as
    "%3A" or already percent-decoded to ':' - handle both.
    """
    if "%3A" in stream_id:
        return stream_id.split("%3A")
    return stream_id.split(":")


def strip_accents(string):
    """Remove diacritics, e.g. 'ação' -> 'acao'."""
    return "".join(
        c for c in unicodedata.normalize("NFD", string) if unicodedata.category(c) != "Mn"
    )


def hr_size(size):
    """Bytes to human readable:
    https://stackoverflow.com/a/43690506"""
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.2f}{unit}"


def safe_get(list_, idx, default=""):
    try:
        return list_[idx]
    except IndexError:
        return default


def num_extract(string):
    num_chars = [ch if ch.isdigit() else ' ' for ch in string]
    return ''.join(num_chars).split()


def is_year(string):
    try:
        if (1850 <= int(string) <= 2050) and len(str(string)) == 4:
            return True
        return False
    except ValueError:
        return False


def sanitize(string, valid_chars=". ", lower=True):
    """Return alphanumeric input with certain non alphanumeric chars intact"""
    # Deixamos os acentos intactos aqui para o meta.py poder criar as duas versões
    chars = [ch if ch.isalnum() or ch in valid_chars else " " for ch in string]
    result = " ".join("".join(chars).split())
    return result.lower() if lower else result


def req_wrapper(url, time_out=3):
    timeout = requests.exceptions.Timeout
    conn_err = requests.exceptions.ConnectionError

    cached_session = Pickle("requests_session.pickle")

    if cached_session.contents:
        req_session = cached_session.contents
    else:
        req_session = requests.session()
        req_session.headers = {
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36"
        }

    try:
        result = req_session.get(f"https://{url}", timeout=time_out).text
        cached_session.contents = req_session
        cached_session.save()
        return result
    except (timeout, conn_err) as e:
        logger.warning("Request to %s failed: %s", url, e)
        return ""


def req_api(url, key="meta"):
    r = req_wrapper(url)
    if not r:
        return dict()
    try:
        return json.loads(r[r.find("{") :].rstrip(")")).get(key)
    except json.decoder.JSONDecodeError as e:
        logger.warning("Couldn't decode JSON response from %s: %s", url, e)
        return dict()
