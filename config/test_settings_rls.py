"""Customer accounts 1c: the whole test suite on PostgreSQL, under row-level
security, as the portal's restricted login.

Runs on Server B in a throw-away container, against a throw-away test
database (test_<name>), never the live one:

    docker compose run --rm -T --no-deps web python manage.py test \\
        --settings=config.test_settings_rls --noinput

Test setup (creating rows directly) runs as system, as Celery and commands
do; every request a test makes goes through the login check and sees only
its login's account. Needs the role avx_portal (scripts/portal_db_role.sh).
"""
from config.settings import *  # noqa: F401,F403
from config.settings import DATABASES as _DB

DATABASES = {'default': {**_DB['default'], 'TEST': {'NAME': 'test_rls_call_platform'}}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BROKER_URL = 'memory://'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}

TENANT_GUARD = True
TEST_RUNNER = 'config.rls_test_runner.RLSTestRunner'

# The same run without row-level security (owner login, no guard): what is
# left failing there comes from PostgreSQL itself, not from 1c.
import os as _os
if _os.environ.get('RLS_OFF') == '1':
    TENANT_GUARD = False
    TEST_RUNNER = 'django.test.runner.DiscoverRunner'
