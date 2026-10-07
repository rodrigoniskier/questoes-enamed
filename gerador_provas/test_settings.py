"""Isolated settings: no real database, SMTP or paid AI calls."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "isolated-tests-only-not-for-deployment")
from .settings import *

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
GEMINI_API_KEY = ""
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
