"""Revoke the temp test partner logins and reject the junk access requests.

The test partners were invited on mailnesia.com addresses, whose inboxes are
publicly readable - anyone who guesses the address can reset the password.
Deactivating the logins keeps the records and history; only sign-in dies.

    docker compose exec web python scripts/cleanup_test_partners.py
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

from accounts.models import User  # noqa: E402
from accounts.access_requests import AccessRequest  # noqa: E402

TEST_LOCAL_PARTS = ('mkwbuyer1', 'adc11test', 'mkwpub1', 'mkwadc11')
JUNK_REQUEST_EMAILS = ('keozx-audit-test@proton.me', 'probe_kx_03@example.com')

for user in User.objects.filter(email__iendswith='@mailnesia.com'):
    local = user.email.split('@')[0].lower()
    if local in TEST_LOCAL_PARTS:
        if user.is_active:
            user.is_active = False
            user.save(update_fields=['is_active'])
            print(f'deactivated: {user.email} ({user.role})')
        else:
            print(f'already off: {user.email} ({user.role})')

for req in AccessRequest.objects.filter(email__in=JUNK_REQUEST_EMAILS, status='pending'):
    req.status = 'rejected'
    req.rejection_reason = 'Probe / spam submission'
    req.save(update_fields=['status', 'rejection_reason'])
    print(f'rejected request: {req.email} ({req.name} / {req.company})')

left = AccessRequest.objects.filter(status='pending').count()
print(f'pending requests remaining: {left} (evaslangat@gmail.com is left alone - looks real)')
