"""Blocked caller smoke (CH-136) - the owner's rule over real HTTP.

    Locally:     docker compose exec -T web python scripts/blocked_caller_smoke.py
    On server:   cd /opt/call_platform && docker compose exec -T web python scripts/blocked_caller_smoke.py

What it proves, through the same endpoint Asterisk calls (/api/twilio/asterisk/route/):
  1. A caller on Blocked Numbers is hung up with "CallerId Blocked" and leaves
     NO call row - dialing again and again still leaves nothing (CH-206).
  2. The same for a number saved by the Blocked Numbers page ("scope=number").
  3. A different refusal (caller flagged as spam) is still FAILED, unchanged.

Safe on the live server: it builds its own throw-away organization, campaign
and number inside one database transaction and rolls everything back at the
end - no rows are left behind and no real money is charged (the billing call
is replaced by a stub for the duration of the test). No phone call is made.
"""
import json
import os
import re
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    src = open(os.path.join(ROOT, 'manage.py')).read()
    os.environ['DJANGO_SETTINGS_MODULE'] = re.search(
        r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([^'\"]+)", src).group(1)
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402

settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
# The endpoint only answers callers that know the Asterisk secret. A laptop
# usually has none in .env, so the test sets one for itself.
if not getattr(settings, 'ASTERISK_SHARED_SECRET', ''):
    settings.ASTERISK_SHARED_SECRET = 'local-smoke-secret'
SECRET = settings.ASTERISK_SHARED_SECRET

from accounts.models import Organization  # noqa: E402
from billing.services import BillingService  # noqa: E402
from campaigns.models import Campaign  # noqa: E402
from phone_numbers.models import PhoneNumber  # noqa: E402
from routing.engine import RoutingEngine  # noqa: E402
from routing.models import CallLog  # noqa: E402
from spam_protection.models import Blacklist  # noqa: E402
import tasks  # noqa: E402

fail = 0


def check(label, got, want):
    global fail
    ok = got == want
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r} (want {want!r})")


class Rollback(Exception):
    pass


def ring(client, caller, called):
    """What Asterisk sends when a call comes in."""
    r = client.post(
        '/api/twilio/asterisk/route/',
        data=json.dumps({'caller_number': caller, 'called_number': called,
                         'asterisk_call_id': f'smoke-{uuid.uuid4()}'}),
        content_type='application/json',
        HTTP_X_ASTERISK_SECRET=SECRET,
    )
    return r.status_code, json.loads(r.content or b'{}')


tag = uuid.uuid4().hex[:8]
blocked_caller = '+1555' + str(uuid.uuid4().int)[:7]
page_caller = '+1557' + str(uuid.uuid4().int)[:7]
spam_caller = '+1556' + str(uuid.uuid4().int)[:7]
tfn = '+1888' + str(uuid.uuid4().int)[:7]

# Stub the money: record what would be charged, charge nothing.
charged = []
real_charge = BillingService.charge_call
real_cost = BillingService.rejected_call_cost
BillingService.charge_call = staticmethod(lambda *a, **k: charged.append(k) or None)
BillingService.rejected_call_cost = staticmethod(lambda org: 1)

try:
    with transaction.atomic():
        org = Organization.objects.create(name=f'SMOKE {tag}', slug=f'smoke-{tag}')
        camp = Campaign.objects.create(organization=org, name=f'SMOKE {tag}', status='active')
        PhoneNumber.objects.create(organization=org, number=tfn, campaign=camp, status='active')
        Blacklist.objects.create(organization_id=org.id, phone_number=blocked_caller, reason='manual')
        Blacklist.objects.create(organization_id=org.id, phone_number=page_caller, reason='scope=number')
        Blacklist.objects.create(organization_id=org.id, phone_number=spam_caller, reason='spam')
        c = Client()

        # 1. Blocked caller rings five times: hung up, nothing on the portal.
        for _ in range(5):
            code, body = ring(c, blocked_caller, tfn)
        check('route answered                ', code, 200)
        check('call hung up (not forwarded)  ', body.get('action'), 'hangup')
        check('reason sent to Asterisk       ', body.get('reason'), RoutingEngine.BLOCKED_CALLER_LABEL)
        check('no call row for blocked caller', CallLog.objects.filter(caller_number=blocked_caller).count(), 0)

        # 2. A number saved by the Blocked Numbers page: same.
        code, body = ring(c, page_caller, tfn)
        check('page block hung up            ', body.get('action'), 'hangup')
        check('no call row for page block    ', CallLog.objects.filter(caller_number=page_caller).count(), 0)

        # 3. Other refusals are untouched: spam stays failed.
        code, body = ring(c, spam_caller, tfn)
        log2 = CallLog.objects.filter(caller_number=spam_caller).first()
        check('spam reason unchanged         ', body.get('reason'), 'Caller flagged as spam')
        check('spam row still failed         ', log2 and log2.status, CallLog.Status.FAILED)

        raise Rollback()
except Rollback:
    print('-- test data rolled back, nothing left behind')
finally:
    BillingService.charge_call = real_charge
    BillingService.rejected_call_cost = real_cost

print('ALL PASS' if not fail else f'{fail} FAILED')
sys.exit(1 if fail else 0)
