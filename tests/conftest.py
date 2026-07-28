import os

# sgd/__init__.py needs these to import successfully (SECRET_KEY signs the
# admin session cookie; ADMIN_PASSWORD gates /admin; GOOGLE_CLIENT_ID/SECRET
# are read lazily by the oauth routes, not at import time, but are set here
# too for tests that exercise them). None of this hits the network.
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-client-secret")

if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

# No POSTGRES_URL/DATABASE_URL on purpose: sgd/__init__.py catches the
# resulting connection error at import time and logs it instead of
# crashing, since individual tests that need a real GoogleDrive instance
# build one directly (see test_gdrive_qgen.py) rather than going through
# the DB-backed multi-tenant lookup.
