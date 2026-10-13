"""The owner's acceptance test for Members -> Remove, over real HTTP.

Create a buyer member, prove their login works, remove them as the admin,
then prove: the open session dies on its next request, the password no
longer signs in, the account is gone from the server, and the same email
can be taken fresh.

    docker compose exec web python scripts/member_removal_smoke.py
"""
import json
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

from django.test import Client  # noqa: E402
from rest_framework_simplejwt.tokens import AccessToken  # noqa: E402
from accounts.models import User  # noqa: E402
from buyers.models import Buyer  # noqa: E402

EMAIL = 'removal-smoke@local.test'
PASSWORD = 'Smoke-removal-1x'

admin = User.objects.filter(role='admin', is_active=True,
                            organization__isnull=False).first() \
    or User.objects.filter(is_superuser=True).first()
buyer = Buyer.objects.filter(organization=admin.organization).first()
print(f'admin: {admin.email} | buyer: {buyer.name} ({buyer.id})')

User.objects.filter(email=EMAIL).delete()  # clean slate from earlier runs
member = User.objects.create(email=EMAIL, username=EMAIL, role='buyer',
                             organization=admin.organization, buyer=buyer,
                             is_active=True)
member.set_password(PASSWORD)
member.save()

c = Client()
fail = 0


def check(label, got, want):
    global fail
    ok = got == want
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got} (want {want})")


member_token = str(AccessToken.for_user(member))  # the "open browser tab"
r = c.get('/api/accounts/me', HTTP_AUTHORIZATION=f'Bearer {member_token}')
check('member session works     ', r.status_code, 200)

r = c.post(f'/api/buyers/{buyer.id}/members/remove',
           data=json.dumps({'email': EMAIL}), content_type='application/json',
           HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
check('admin removes member     ', r.status_code, 200)

r = c.get('/api/accounts/me', HTTP_AUTHORIZATION=f'Bearer {member_token}')
check('open tab now refused     ', r.status_code, 401)

r = c.post('/api/accounts/login',
           data=json.dumps({'email': EMAIL, 'password': PASSWORD}),
           content_type='application/json')
ok = r.status_code != 200
fail += 0 if ok else 1
print(f"{'PASS' if ok else 'FAIL'}  old password refused     : {r.status_code} (want not 200)")

gone = not User.objects.filter(email__iexact=EMAIL).exists()
fail += 0 if gone else 1
print(f"{'PASS' if gone else 'FAIL'}  account gone from server : {gone} (want True)")

fresh = User.objects.create(email=EMAIL, username=EMAIL, role='buyer',
                            organization=admin.organization, buyer=buyer)
print('PASS  same email taken fresh   : new account created cleanly')
fresh.delete()

# The same fix covers deactivation everywhere: a switched-off account's
# token must die on its next request too.
staffish = User.objects.create(email='inactive-smoke@local.test',
                               username='inactive-smoke@local.test',
                               role='manager', organization=admin.organization,
                               is_active=False)
r = c.get('/api/accounts/me', HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(staffish)}')
check('deactivated token refused', r.status_code, 401)
staffish.delete()

print('ALL GREEN' if fail == 0 else f'{fail} FAILURES')
sys.exit(1 if fail else 0)
