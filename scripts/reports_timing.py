"""Time the Reports requests for one past day and one month, over real HTTP.

Run BEFORE deploying a performance change and again AFTER - the pair of
outputs is the proof.

    docker compose exec web python scripts/reports_timing.py
"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    src = open(os.path.join(ROOT, 'manage.py')).read()
    os.environ['DJANGO_SETTINGS_MODULE'] = re.search(
        r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([^'\"]+)", src).group(1)

import django  # noqa: E402
django.setup()

from django.conf import settings  # noqa: E402
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from django.test import Client  # noqa: E402
from rest_framework_simplejwt.tokens import AccessToken  # noqa: E402
from accounts.models import User  # noqa: E402

admin = User.objects.filter(role='admin', is_active=True,
                            organization__isnull=False).first()
auth = {'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(admin)}'}
c = Client()

DAY = 'date_from=2026-09-15&date_to=2026-09-15'
MONTH = 'date_from=2026-09-15&date_to=2026-10-14'
TZ = 'timezone=America/New_York'


def hit(label, path):
    t0 = time.monotonic()
    r = c.get(path, **auth)
    ms = int((time.monotonic() - t0) * 1000)
    size = len(r.content)
    print(f'{ms:6d} ms  {r.status_code}  {size:>9,}B  {label}')
    return ms


for span, name in ((DAY, 'day  '), (MONTH, 'month')):
    print(f'--- {name} ({span.split("&")[0][10:]} ..) ---')
    hit(f'{name} calls page 1 (500)   ', f'/api/analytics/calls?{span}&{TZ}&limit=500&offset=0')
    hit(f'{name} calls deep page (p10)', f'/api/analytics/calls?{span}&{TZ}&limit=500&offset=4500')
    for ep in ('campaigns', 'buyers', 'publishers', 'carriers'):
        hit(f'{name} summary {ep:10s}  ', f'/api/analytics/{ep}?{span}&{TZ}')
print('note: a repeat run of a past range shows the Redis-cache time once deployed')
