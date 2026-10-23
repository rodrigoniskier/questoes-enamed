"""Isolated settings: no real database, SMTP or paid AI calls."""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("DJANGO_SECRET_KEY", "isolated-tests-only-not-for-deployment")
from .settings import *

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": str(Path(tempfile.gettempdir()) / f"questoes-enamed-tests-{os.getpid()}.sqlite3")},
        "OPTIONS": {"timeout": 20},
    }
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
GEMINI_API_KEY = ""
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
