"""Show who has been hitting the public access-request form.

    docker compose exec web python scripts/access_request_audit.py
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

from accounts.access_requests import AccessRequest  # noqa: E402

total = AccessRequest.objects.count()
pending = AccessRequest.objects.filter(status='pending').count()
print(f'access requests: {total} total, {pending} pending')
for r in AccessRequest.objects.order_by('-created_at')[:30]:
    print(f'{r.created_at:%m-%d %H:%M}  {r.status:8s}  {r.email:40s}  {r.name} / {r.company}')
