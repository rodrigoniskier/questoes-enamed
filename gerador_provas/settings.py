# gerador_provas/settings.py
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("Configure DJANGO_SECRET_KEY no ambiente.")

DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"

ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "rodrigoniskier.pythonanywhere.com").split(",")

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "questoes.apps.QuestoesConfig",
    "bulk_submit",
    "django_cleanup.apps.CleanupConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "gerador_provas.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                # --- CORREÇÃO ESTÁ NESTA LINHA ---
                "django.contrib.messages.context_processors.messages",  # O correto é 'context_processors.messages'
            ],
        },
    },
]
WSGI_APPLICATION = "gerador_provas.wsgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("DJANGO_DATABASE_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Recife"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static"),
]

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = ["https://*.pythonanywhere.com", "https://rodrigoniskier.pythonanywhere.com"]

JAZZMIN_SETTINGS = {
    "site_title": "QUESTÕES MEDICINA",
    "site_header": "QUESTÕES MEDICINA",
    "site_brand": "QUESTÕES MEDICINA",
    "site_logo": "images/naped.jpg",
    "login_logo": "images/logo.jpg",
    "login_logo_max_size": "250px",
    "welcome_sign": "Bem-vindo ao Gerador de Provas do curso de Medicina",
    "copyright": "Desenvolvido por Prof. Rodrigo Niskier",
    "custom_css": "admin_custom.css",
}
JAZZMIN_UI_TWEAKS = {
    "theme": "darkly",
    "body_classes": "gradient-bg",
}
# Em: gerador_provas/settings.py

# Configuração de E-mail para Desenvolvimento
# (Imprime os e-mails no console onde o 'runserver' está rodando)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER  # Garante que o remetente seja o mesmo

# Em: gerador_provas/settings.py (no final)

# Chave de API do Google Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
