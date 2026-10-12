"""Phase 2 smoke: every newly guarded area answers 200 with the permission
ticked and 403 with it unticked, through real HTTP, as a manager.

Run inside the web container:
    docker compose exec web python scripts/phase2_smoke.py

Self-contained: finds the org, seeds built-in roles, mints its own tokens,
edits only the temp smoke user's role caps and restores them afterwards.
"""
import os
import re
import sys

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

# The test client sends Host: testserver, which production ALLOWED_HOSTS
# rejects with a bare 400 before any view runs.
from django.conf import settings  # noqa: E402
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from django.test import Client  # noqa: E402
from rest_framework_simplejwt.tokens import AccessToken  # noqa: E402
from accounts.models import User, CustomRole  # noqa: E402
from accounts.workspace_permissions import ensure_builtin_roles, ALL_KEYS  # noqa: E402

# One list endpoint per area Phase 2 guarded. call-flows spans three routers,
# so all three list routes are checked under the one key.
AREAS = {
    'tracking-numbers': ['/api/numbers/'],
    'number-pools': ['/api/dni/pools'],
    'call-flows': ['/api/routing/rules', '/api/ivr/flows', '/api/queue/'],
    'destinations': ['/api/destinations/'],
    'buyers': ['/api/buyers/'],
    'traffic-sources': ['/api/publishers/'],
    'blocked-numbers': ['/api/spam/blacklist'],
    'billing': ['/api/billing/account'],
    'integrations': ['/api/integrations/'],
    'rtb-bid-log': ['/api/rtb/auctions'],
}

admin = User.objects.filter(is_superuser=True, organization__isnull=False).first()
org = admin.organization
ensure_builtin_roles(org)

smoke, _ = User.objects.get_or_create(
    email='phase2smoke@local.test',
    # username is unique on the users table and other rows hold '', so the
    # default '' collides; the email is already unique, reuse it.
    defaults={'role': 'manager', 'organization': org, 'is_active': True,
              'username': 'phase2smoke@local.test'},
)
if smoke.role != 'manager' or smoke.organization_id != org.id or not smoke.is_active:
    smoke.role, smoke.organization, smoke.is_active = 'manager', org, True
    smoke.save()

manager_role = CustomRole.objects.get(organization=org, slug='manager')
original_caps = list(manager_role.capabilities)

client = Client()


def hit(path, user):
    auth = f'Bearer {AccessToken.for_user(user)}'
    return client.get(path, HTTP_AUTHORIZATION=auth, follow=True).status_code


failures = 0
try:
    for area, paths in AREAS.items():
        manager_role.capabilities = list(ALL_KEYS)
        manager_role.save(update_fields=['capabilities'])
        with_perm = [hit(p, smoke) for p in paths]

        manager_role.capabilities = [k for k in ALL_KEYS if not k.startswith(area + '.')]
        manager_role.save(update_fields=['capabilities'])
        without_perm = [hit(p, smoke) for p in paths]

        ok = all(s != 403 for s in with_perm) and all(s == 403 for s in without_perm)
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {area:17s} ticked={with_perm} unticked={without_perm}")

    manager_role.capabilities = original_caps
    manager_role.save(update_fields=['capabilities'])

    admin_codes = {a: [hit(p, admin) for p in ps] for a, ps in AREAS.items()}
    bad = {a: c for a, c in admin_codes.items() if any(s == 403 for s in c)}
    print('PASS  admin sees every area' if not bad else f'FAIL  admin got 403: {bad}')
    failures += 1 if bad else 0

    partner = User.objects.filter(role='buyer', is_active=True).first()
    if partner:
        code = hit('/api/destinations/', partner)
        print(f"{'PASS' if code != 403 else 'FAIL'}  partner pass-through (buyer got {code}, not 403)")
        failures += 1 if code == 403 else 0
finally:
    manager_role.capabilities = original_caps
    manager_role.save(update_fields=['capabilities'])

print('ALL GREEN' if failures == 0 else f'{failures} FAILURES')
sys.exit(1 if failures else 0)
