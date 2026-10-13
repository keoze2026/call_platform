"""Owner M1 smoke: organizations CRUD, money moves, suspension that bites.

    docker compose exec web python scripts/owner_m1_smoke.py

Creates one throwaway organization, exercises every M1 endpoint over real
HTTP as the owner, proves a suspended org's member is locked out, then
deletes the throwaway org. Also proves guest tracking records a visit.
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
from accounts.models import User, Organization  # noqa: E402

owner = User.objects.filter(is_superuser=True).first()
print('owner:', owner.email)
c = Client()
fail = 0


def call(label, method, path, body=None, user=owner, want=200):
    global fail
    kw = {'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(user)}'}
    if body is not None:
        kw.update(data=json.dumps(body), content_type='application/json')
    r = getattr(c, method)(path, **kw)
    ok = r.status_code == want
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {r.status_code} (want {want})  {r.content[:110]}")
    return r


r = call('list organizations   ', 'get', '/api/owner/organizations')
r = call('create org           ', 'post', '/api/owner/organizations',
         {'name': 'M1 Smoke Org', 'per_minute_rate': '0.5000'}, want=201)
org_id = json.loads(r.content)['id']
call('duplicate name 400   ', 'post', '/api/owner/organizations', {'name': 'M1 Smoke Org'}, want=400)
call('detail               ', 'get', f'/api/owner/organizations/{org_id}')
call('patch rates          ', 'patch', f'/api/owner/organizations/{org_id}',
     {'markup_percent': '10.00', 'monthly_portal_fee': '0'})
call('bad rate 400         ', 'patch', f'/api/owner/organizations/{org_id}',
     {'per_minute_rate': 'banana'}, want=400)
r = call('credit 25            ', 'post', f'/api/owner/organizations/{org_id}/credit',
         {'amount': '25.00', 'note': 'smoke'})
r = call('debit 10             ', 'post', f'/api/owner/organizations/{org_id}/debit',
         {'amount': '10.00', 'note': 'smoke'})
balance = json.loads(r.content)['balance']
ok = balance == '15.00'
fail += 0 if ok else 1
print(f"{'PASS' if ok else 'FAIL'}  balance arithmetic: {balance} (want 15.00)")

org = Organization.objects.get(id=org_id)
member = User.objects.create(email='m1smoke@local.test', username='m1smoke@local.test',
                             role='admin', organization=org, is_active=True)
call('member works         ', 'get', '/api/accounts/me', user=member)
call('suspend              ', 'post', f'/api/owner/organizations/{org_id}/suspend')
call('member locked out    ', 'get', '/api/accounts/me', user=member, want=401)
call('activate             ', 'post', f'/api/owner/organizations/{org_id}/activate')
call('member back          ', 'get', '/api/accounts/me', user=member)
call('non-owner refused    ', 'get', '/api/owner/organizations', user=member, want=403)

# Guest tracking: push one anonymous request through the middleware.
from django.test import RequestFactory  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from analytics.guest_tracking import GuestVisitMiddleware  # noqa: E402
from analytics.models import GuestVisit  # noqa: E402

mw = GuestVisitMiddleware(lambda req: HttpResponse('ok'))
req = RequestFactory().get('/pricing', HTTP_CF_CONNECTING_IP='203.0.113.77',
                           HTTP_CF_IPCOUNTRY='KE')
mw(req)
mw(req)
row = GuestVisit.objects.filter(ip='203.0.113.77', page='/pricing').first()
ok = row is not None and row.count == 2 and row.country == 'KE'
fail += 0 if ok else 1
print(f"{'PASS' if ok else 'FAIL'}  guest visit counted: "
      f"{row.ip if row else None} {row.country if row else None} x{row.count if row else 0} (want x2 KE)")
call('guest-visits view    ', 'get', '/api/owner/guest-visits?q=203.0.113.77')

GuestVisit.objects.filter(ip='203.0.113.77').delete()
org.delete()  # cascades the smoke org, its billing account and member
print('cleaned up smoke org and visit rows')
print('ALL GREEN' if fail == 0 else f'{fail} FAILURES')
sys.exit(1 if fail else 0)
