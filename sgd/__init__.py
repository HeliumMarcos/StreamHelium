import os
import logging
from flask import Flask

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    raise RuntimeError(
        "The SECRET_KEY environment variable is not set. It signs the admin "
        "login session cookie. Generate one with: "
        'python -c "import secrets; print(secrets.token_hex(32))"'
    )
app.secret_key = secret_key

from sgd import db  # noqa: E402  (needs `app` to already exist for logging config)

try:
    db.init_schema()
except Exception as e:
    # Don't crash the whole app on cold start if the DB is briefly
    # unreachable - individual routes that need it will fail loudly on
    # their own, which is easier to diagnose than a dead deployment.
    logger.error("Could not initialize/verify the users table: %s", e)

from sgd import home  # noqa: E402
from sgd import routes  # noqa: E402
from sgd import oauth  # noqa: E402
from sgd import admin  # noqa: E402
from sgd import family_auth  # noqa: E402
from sgd import proxy_token  # noqa: E402
