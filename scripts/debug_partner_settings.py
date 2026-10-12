"""Reproduce Evans's partner-settings 500 inside the container and print the
real traceback. Read-only: the PATCH sets a value that is already the default.

    docker compose exec web python scripts/debug_partner_settings.py
"""
import json
import os
import re
import sys
import traceback

# `python scripts/foo.py` puts scripts/ on sys.path, not the project root, so
# the settings package is invisible without this.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    src = open(os.path.join(ROOT, 'manage.py')).read()
    os.environ['DJANGO_SETTINGS_MODULE'] = re.search(
        r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([^'\"]+)", src).group(1)

import django  # noqa: E402
django.setup()

from django.test import Client  # noqa: E402
from rest_framework_simplejwt.tokens import AccessToken  # noqa: E402
from accounts.models import User  # noqa: E402
from publishers.models import Publisher  # noqa: E402
from buyers.models import Buyer  # noqa: E402

u = User.objects.filter(role='admin', is_active=True).first() \
    or User.objects.filter(is_superuser=True).first()
print('as user:', u.email, '| role:', u.role, '| org:', u.organization_id)
auth = {'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(u)}'}

pub = Publisher.objects.filter(id='03970c24-995d-4601-9f21-bc88c3af3f61').first() \
    or Publisher.objects.filter(organization=u.organization).first()
buy = Buyer.objects.filter(organization=u.organization).first()
print('publisher:', pub.id, '| buyer:', buy.id)

c = Client(raise_request_exception=False)
for label, method, path, body in [
    ('catalogue ', 'get',   '/api/accounts/partner-permissions', None),
    ('pub GET   ', 'get',   f'/api/accounts/publisher/{pub.id}/settings', None),
    ('buy GET   ', 'get',   f'/api/accounts/buyer/{buy.id}/settings', None),
    ('pub PATCH ', 'patch', f'/api/accounts/publisher/{pub.id}/settings',
     {'permissions': {'download_reports': True}}),
]:
    kw = dict(auth)
    if body is not None:
        kw.update(data=json.dumps(body), content_type='application/json')
    r = getattr(c, method)(path, **kw)
    print(f'{label}: {r.status_code}  {r.content[:150]}')

try:
    Client().get(f'/api/accounts/publisher/{pub.id}/settings', **auth)
    print('no exception raised here - the 500 may only happen on the live '
          'worker (old code still loaded); restart and retest')
except Exception:
    print('==== TRACEBACK ====')
    traceback.print_exc()
