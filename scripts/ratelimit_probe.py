"""Prove the rate limiter counts and trips through the shared Redis cache.

Simulates five POSTs from one Cloudflare visitor at a 3-per-hour limit:
the first three must pass, the fourth and fifth must be limited.

    docker compose exec web python scripts/ratelimit_probe.py
"""
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

import time  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django_ratelimit.decorators import is_ratelimited  # noqa: E402
from config.ratelimit import client_ip  # noqa: E402

# A fresh group per run, so earlier runs never pre-fill the window.
group = f'probe_{int(time.time())}'
rf = RequestFactory()

req = rf.post('/probe', HTTP_CF_CONNECTING_IP='203.0.113.9')
print('key resolves to:', client_ip(group, req))

flags = [
    is_ratelimited(req, group=group, key='config.ratelimit.client_ip',
                   rate='3/h', method='POST', increment=True)
    for _ in range(5)
]
print('limited flags:', flags, '(want [False, False, False, True, True])')

other = rf.post('/probe', HTTP_CF_CONNECTING_IP='203.0.113.10')
separate = is_ratelimited(other, group=group, key='config.ratelimit.client_ip',
                          rate='3/h', method='POST', increment=True)
print('different visitor:', separate, '(want False - budgets are per visitor)')

ok = flags == [False, False, False, True, True] and not separate
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
