"""Symmetric encryption for secrets stored at rest (Google refresh tokens,
TMDB API keys) in Postgres.

Generate a key once with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and set it as the ENCRYPTION_KEY env var. Losing this key makes every stored
token unrecoverable - users would need to reconnect their Google Drive.
"""

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


@lru_cache(maxsize=1)
def _fernet():
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "The ENCRYPTION_KEY environment variable is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str | None:
    if value is None:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str | None:
    if value is None:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError(
            "Failed to decrypt a stored secret - ENCRYPTION_KEY may have "
            "changed since it was written."
        ) from e
