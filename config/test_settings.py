"""Settings for the automatic test run (CH-354, Avortyx Guard): the whole
suite on an in-memory database, no Redis, no Celery broker, no .env needed.

    python manage.py test --settings=config.test_settings --noinput
"""
from config.settings import *  # noqa: F401,F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BROKER_URL = 'memory://'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
