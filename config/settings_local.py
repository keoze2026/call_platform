"""Local development settings: no Docker, no Redis, no Postgres needed.

Save as config/settings_local.py in the call_platform folder, then in each new
PowerShell window run:
    $env:DJANGO_SETTINGS_MODULE = "config.settings_local"

Do not commit this file and never use it on the server.
"""
import os

# settings.py reads these from the environment first, so setting them here
# means no .env file is needed (and no C:\opt\call_platform copy either).
_defaults = {
    "DEBUG": "True",
    "SECRET_KEY": "local-dev-only-3f9a7c1e5b2d4086a1c7e9f0b3d5a7c9",
    "ENVIRONMENT": "development",
    "ALLOWED_HOSTS": "localhost,127.0.0.1",
    "CORS_ALLOWED_ORIGINS": "http://localhost:3000,http://127.0.0.1:3000",
    "CSRF_TRUSTED_ORIGINS": "http://localhost:3000,http://127.0.0.1:3000",
    "FRONTEND_URL": "http://localhost:3000",
    "PUBLIC_SITE_URL": "http://localhost:3000",
    "MEDIA_BASE_URL": "http://localhost:8000",
    "API_THROTTLE_ANON": "600/m",
    "API_THROTTLE_USER": "6000/m",
}
for _key, _value in _defaults.items():
    os.environ.setdefault(_key, _value)

from .settings import *  # noqa: E402,F401,F403

# Database: SQLite file in the project folder (db.sqlite3, already git-ignored)
# unless DATABASE_URL is set in the environment.
if not os.environ.get("DATABASE_URL"):
    DATABASES = {  # noqa: F405
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
        }
    }

# Redis replacements: in-process cache and channel layer.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "callplatform-local",
    }
}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Background tasks run inline instead of going to a Redis broker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# Emails print in this terminal instead of trying to send.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Plain http locally.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

# django-ratelimit warns that a local-memory cache is not shared between
# processes; that is fine for one dev server.
SILENCED_SYSTEM_CHECKS = ["django_ratelimit.W001", "django_ratelimit.E003"]
