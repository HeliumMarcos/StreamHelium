import os
import json
import logging
from flask import Flask
from sgd.gdrive import GoogleDrive

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

token_from_env = os.environ.get("TOKEN")
if not token_from_env:
    raise RuntimeError(
        "The TOKEN environment variable is not set. It must contain the "
        "Google OAuth credentials JSON obtained during setup (see README.md)."
    )
try:
    token = json.loads(token_from_env)
except json.JSONDecodeError as e:
    raise RuntimeError(
        f"The TOKEN environment variable does not contain valid JSON: {e}"
    ) from e

gdrive = GoogleDrive(token)

from sgd import routes
