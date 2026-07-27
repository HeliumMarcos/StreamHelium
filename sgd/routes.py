import os
import re
import logging
from sgd import app, gdrive, home
from sgd.meta import MetadataNotFound, Meta
from sgd.streams import Streams
from sgd.utils import split_stream_id, hr_size
from json import dumps
from flask import jsonify, abort, Response, redirect, request
from datetime import datetime

logger = logging.getLogger(__name__)

# tt1234567 for IMDb ids, or tmdb:1234 for TMDB ids, optionally followed by
# :<season>:<episode> for series.
VALID_IMDB_ID = re.compile(r"^tt\d{5,10}$", re.IGNORECASE)
VALID_SEASON_EPISODE = re.compile(r"^\d+$")


def is_valid_stream_id(stream_id):
    parts = split_stream_id(stream_id)
    base_id = parts[0].lower()

    if base_id == "tmdb":
        if len(parts) < 2 or not parts[1].isdigit():
            return False
        extra = parts[2:]
    elif VALID_IMDB_ID.match(base_id):
        extra = parts[1:]
    else:
        return False

    return len(extra) <= 2 and all(VALID_SEASON_EPISODE.match(p) for p in extra)


MANIFEST = {
    "id": "streamhelium.stremio.googledrive",
    "version": "1.1.5",
    "name": "Stream Helium",
    "description": "Este plugin busca Filmes e Séries.",
"favicon": "https://catalogo.heliummarcos.com.br/addon/favicon.png",
    "logo": "https://catalogo.heliummarcos.com.br/addon/logo2.png",
    "background": "https://catalogo.heliummarcos.com.br/addon/background2.png",
    "resources": ["stream"],
    "types": ["movie", "series"],
    "catalogs": [],
}


def manifest_url():
    """The public https URL of this deployment's manifest.

    Vercel terminates TLS in front of the app, so request.url_root comes
    through as http:// - force https so the link we hand to Stremio works.
    """
    return f"https://{request.host}/manifest.json"


@app.route("/")
def init():
    page = home.render(
        manifest=MANIFEST,
        manifest_url=manifest_url(),
        stremio_url=f"stremio://{request.host}/manifest.json",
        tmdb_enabled=bool(os.environ.get("TMDB_API_KEY")),
        proxy_enabled=bool(os.environ.get("CF_PROXY_URL")),
    )
    resp = Response(page, mimetype="text/html; charset=utf-8")
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def mask_email(email):
    """h****s@gmail.com - the page is public, the owner's address isn't."""
    if not email or "@" not in email:
        return email or ""
    user, _, domain = email.partition("@")
    if len(user) <= 2:
        return f"{user[0]}*@{domain}"
    return f"{user[0]}{'*' * (len(user) - 2)}{user[-1]}@{domain}"


def drive_status():
    """A cheap liveness check against the Drive API for the landing page."""
    try:
        about = (
            gdrive.drive_instance.about()
            .get(fields="user(displayName,emailAddress),storageQuota")
            .execute()
        )
    except Exception as e:
        logger.warning("Drive health check failed: %s", e)
        return {"connected": False, "error": type(e).__name__}

    user = about.get("user", {})
    quota = about.get("storageQuota", {})
    status = {
        "connected": True,
        "account": mask_email(user.get("emailAddress")) or user.get("displayName"),
    }

    try:
        usage = int(quota.get("usage", 0))
        status["usage_human"] = hr_size(usage)
        limit = quota.get("limit")
        if limit:
            limit = int(limit)
            status["limit_human"] = hr_size(limit)
            status["usage_pct"] = round(usage / limit * 100, 1) if limit else None
    except (TypeError, ValueError):
        pass

    try:
        drives = gdrive.drive_instance.drives().list(
            pageSize=100, fields="drives(id)"
        ).execute()
        status["shared_drives"] = len(drives.get("drives", []))
    except Exception as e:
        logger.warning("Couldn't list shared drives: %s", e)

    return status


@app.route("/health")
def health():
    status = drive_status()
    payload = {
        "status": "ok" if status.get("connected") else "degraded",
        "addon": {
            "id": MANIFEST["id"],
            "name": MANIFEST["name"],
            "version": MANIFEST["version"],
            "manifest": manifest_url(),
        },
        "drive": status,
        "config": {
            "tmdb": bool(os.environ.get("TMDB_API_KEY")),
            "cf_proxy": bool(os.environ.get("CF_PROXY_URL")),
        },
    }
    resp = jsonify(payload)
    resp.status_code = 200 if status.get("connected") else 503
    return common_headers(resp)


@app.route("/favicon.png")
@app.route("/favicon.ico")
def favicon():
    return redirect(MANIFEST["favicon"], code=302)


@app.route("/manifest.json")
def addon_manifest():
    return common_headers(jsonify(MANIFEST))


@app.route("/stream/<stream_type>/<stream_id>.json")
def addon_stream(stream_type, stream_id):

    invalid_stream_type = stream_type not in MANIFEST["types"]
    invalid_id = not is_valid_stream_id(stream_id)

    if invalid_stream_type or invalid_id:
        abort(404)
    try:
        resp = Response(
            response=get_streams(stream_type, stream_id),
            mimetype="application/json",
        )
        return common_headers(resp)
    except MetadataNotFound as e:
        logger.info("%s", e)
        abort(404)


def common_headers(resp_obj):
    resp_obj.headers["Access-Control-Allow-Origin"] = "*"
    resp_obj.headers["Access-Control-Allow-Headers"] = "*"
    resp_obj.headers["X-Robots-Tag"] = "noindex"
    return resp_obj


def get_streams(stream_type, stream_id):
    # Stream the response body so the connection stays open (and doesn't hit
    # a request timeout) while we search Drive and score the results.
    yield '{"streams":'

    start_time = datetime.now()
    time_taken = lambda st: f"{(datetime.now() - st).total_seconds():.3f}s"

    stream_meta = Meta(stream_type, stream_id)
    gdrive.search(stream_meta)
    logger.info(
        "Got %d/%d unique results from gdrive after deduping in %s. Scoring results...",
        len(gdrive.results), gdrive.len_response, time_taken(start_time),
    )
    streams = Streams(gdrive, stream_meta)
    logger.info(
        "Fetched %d/%d valid stream(s) in %s for %s -> %s",
        len(streams.results), len(gdrive.results), time_taken(start_time),
        stream_id, gdrive.query,
    )

    yield f"{dumps(streams.results)}}}"
