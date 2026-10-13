"""Prove the two new Stats API endpoints over HTTP with a real API key.

    docker compose exec web python scripts/stats_destinations_smoke.py

Mints a temporary key, exercises /api/stats/destinations (fields, filters)
and /api/stats/buyers (status, concurrency_cap, live_calls, include_idle),
then deletes the key.
"""
import hashlib
import os
import re
import sys

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

import json  # noqa: E402
from django.test import Client  # noqa: E402
from accounts.models import User, APIKey  # noqa: E402

admin = User.objects.filter(role='admin', is_active=True,
                            organization__isnull=False).first() \
    or User.objects.filter(is_superuser=True).first()
raw = APIKey.generate_key()
key = APIKey.objects.create(
    user=admin, organization=admin.organization, name='stats smoke (temp)',
    key_prefix=raw[:12], key_hash=hashlib.sha256(raw.encode()).hexdigest(),
)
print('as:', admin.email)

c = Client()
auth = {'HTTP_AUTHORIZATION': f'Bearer {raw}'}
fail = 0

try:
    r = c.get('/api/stats/destinations', **auth)
    rows = json.loads(r.content)
    ok = r.status_code == 200
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  destinations: {r.status_code}, {len(rows)} rows")
    if rows:
        want = ['id', 'tfn', 'name', 'buyer_id', 'buyer_name', 'enabled',
                'concurrency_cap', 'live_calls', 'daily_cap', 'daily_calls',
                'monthly_cap', 'monthly_calls', 'global_cap', 'global_calls',
                'incoming_calls']
        missing = [k for k in want if k not in rows[0]]
        fail += 1 if missing else 0
        print(f"{'PASS' if not missing else 'FAIL'}  every spec field present"
              + (f' - missing {missing}' if missing else ''))
        sample = rows[0]
        print(f"      sample: {sample['name']} tfn={sample['tfn']} "
              f"incoming={sample['incoming_calls']} daily={sample['daily_calls']}")

    r = c.get('/api/stats/destinations?enabled=true', **auth)
    on_rows = json.loads(r.content)
    ok = r.status_code == 200 and all(x['enabled'] for x in on_rows)
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  enabled filter: {len(on_rows)} rows, all enabled={ok}")

    r = c.get('/api/stats/destinations?has_calls=true&date_from=2026-10-01', **auth)
    hc = json.loads(r.content)
    ok = r.status_code == 200 and all(x['incoming_calls'] > 0 for x in hc)
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  has_calls since Oct 1: {len(hc)} rows, all > 0 calls={ok}")

    r = c.get('/api/stats/buyers?include_idle=true&date_from=2026-10-01', **auth)
    buyers = json.loads(r.content)
    ok = r.status_code == 200 and buyers and all(
        'status' in b and 'concurrency_cap' in b and 'live_calls' in b for b in buyers)
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  buyers+idle: {r.status_code}, {len(buyers)} rows, "
          f"new fields on every row={ok}")
    for b in buyers[:4]:
        print(f"      {b['buyer_name']}: status={b['status']} cc_cap={b['concurrency_cap']} "
              f"live={b['live_calls']} calls={b.get('total_calls', 0)}")

    r = c.get('/api/stats/buyers?date_from=2026-10-01', **auth)
    without = json.loads(r.content)
    ok = r.status_code == 200 and len(without) <= len(buyers)
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  without idle: {len(without)} rows (<= {len(buyers)})")
finally:
    key.delete()
    print('temp key deleted')

print('ALL GREEN' if fail == 0 else f'{fail} FAILURES')
sys.exit(1 if fail else 0)
