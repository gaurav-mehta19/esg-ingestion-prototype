"""
Django settings for the ESG ingestion platform prototype.

Narration for a React-first developer:
- Django settings is the equivalent of a combined .env + app-config file. It is
  read once at process start. Reloading requires restarting the dev server.
- INSTALLED_APPS is the registry of "things Django knows about" — both built-in
  (auth, admin, sessions) and our three apps (core, ingestion, activity).
- DATABASES dict tells the ORM where to write. We point at the Postgres docker
  container from docker-compose.yml.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-do-not-use-in-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "corsheaders",
    # local apps — one per MODEL.md layer
    "apps.core",
    "apps.ingestion",
    "apps.activity",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # must precede CommonMiddleware
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "esg_platform.urls"

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
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "esg_platform.wsgi.application"

# Postgres connection. MODEL.md requires JSONB, CHECK constraints, and
# eventually RLS — all PostgreSQL-specific. Do not switch to SQLite without
# revisiting MODEL.md §2 (multi-tenancy).
#
# Connection string comes from DATABASE_URL in .env (copy .env.example → .env
# on first setup). The dev default below matches the docker-compose Postgres
# so the app still starts if someone forgets the .env step.
_db_url = urlparse(
    os.environ.get(
        "DATABASE_URL",
        "postgres://esg:esg_dev_password@localhost:5432/esg_platform",
    )
)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _db_url.path.lstrip("/"),
        "USER": _db_url.username,
        "PASSWORD": _db_url.password,
        "HOST": _db_url.hostname,
        "PORT": str(_db_url.port or 5432),
    }
}

AUTH_PASSWORD_VALIDATORS: list = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    # Prototype: no auth on API. In production this becomes a JWT or session-auth.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 50,
}

# CORS: allow the Vite dev server to call the Django API in development.
CORS_ALLOW_ALL_ORIGINS = DEBUG
