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

# CH-538: call events are pushed on the channel layer; no Redis in tests.
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}

# CH-619: the suite must not depend on the server's .env. Run on a server (in a
# throwaway container, with that server's environment), these three took the
# live values and 9 tests failed that pass everywhere else. Each is set back
# to its default here; a test that needs another value overrides it itself.
AI_FINAL_ENABLED = False
RECORDING_BASE_URL = ''
ZERO_SECOND_ANSWER_IS_NOT_CONNECTED = True
# CH-634: links unsigned unless a test turns signing on itself.
RECORDING_LINK_SECRET = ''
# CH-670: no live Twilio / Telnyx balance reads from the suite.
BALANCE_CHECKS = False
