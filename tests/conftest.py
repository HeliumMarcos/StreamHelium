import os

# sgd/__init__.py builds a GoogleDrive client at import time and requires a
# valid-looking TOKEN env var to do so. Set a fake one before any `sgd.*`
# module is imported by the test suite. This doesn't hit the network:
# `Credentials.from_authorized_user_info` and the Drive `build()` call only
# need well-formed fields, not real credentials.
os.environ.setdefault(
    "TOKEN",
    '{"token": "fake", "refresh_token": "fake", '
    '"token_uri": "https://oauth2.googleapis.com/token", '
    '"client_id": "fake.apps.googleusercontent.com", "client_secret": "fake", '
    '"scopes": ["https://www.googleapis.com/auth/drive"]}',
)
