"""CH-653: customer accounts - read-only re-check of points 1, 2/4, 3 and 5
on live. Run after a deploy or a server move, next to scripts/point9_check.py,
`same_account --prove` and `recording_links --prove` (first run: the new
Server B, 10 Nov).

  cd /opt/call_platform && docker compose exec -T web python manage.py shell < scripts/isolation_recheck.py

Saves nothing: only SELECTs and GET requests made as a customer login
(the request runs in this process; no browser, no password, no login record).
"""
import json
import re

from django.conf import settings
from django.db import connection
from django.test import Client
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher

bad = 0


def line(ok, text):
    global bad
    bad += 0 if ok else 1
    print(('OK   ' if ok else 'FAIL ') + text)


main = Organization.objects.get(slug=settings.MAIN_ORGANIZATION_SLUG)
customers = list(Organization.objects.exclude(pk=main.pk).exclude(slug=getattr(settings, 'TFN_STOCK_SLUG', '') or None)
                 .order_by('slug'))   # CH-665: the stock account is not a customer
print(f'Main account: {main.slug} | customer accounts: {", ".join(o.slug for o in customers) or "none"}')

# ── Point 1: every row has an account ────────────────────────────────────────
with connection.cursor() as cur:
    cur.execute("select table_name from information_schema.columns "
                "where table_schema='public' and column_name='organization_id' order by 1")
    tables = [r[0] for r in cur.fetchall()]
    empty = []
    for t in tables:
        cur.execute(f'select count(*) from "{t}" where organization_id is null')
        n = cur.fetchone()[0]
        if n:
            empty.append(f'{t}={n}')
    # The one exception, kept apart on purpose (5 Nov): a login with no
    # account (hans@gmail.com) and its own activity rows. Under the account
    # rules it sees nothing. Any other row without an account fails.
    cur.execute('select count(*) from activity_logs where organization_id is null and user_id not in '
                '(select id from users where organization_id is null)')
    stray_activity = cur.fetchone()[0]
allowed = {t for t in ('users', 'activity_logs')}
others = [e for e in empty if e.split('=')[0] not in allowed]
no_account = list(User.objects.filter(organization__isnull=True).values_list('email', flat=True))
print(f'     logins with no account (kept apart, see nothing): '
      f'{", ".join(e[:3] + "***@" + e.split("@")[-1] for e in no_account) or "none"}')
line(not others and stray_activity == 0,
     f'Point 1: {len(tables)} tables carry the account; rows without one: {", ".join(empty) or "none"}'
     ' (only logins with no account and their own activity rows are allowed)')

# ── Point 5: TFN pools ───────────────────────────────────────────────────────
pools = {}
stock_id = Organization.objects.filter(slug=getattr(settings, 'TFN_STOCK_SLUG', '') or None).values_list('id', flat=True).first()
for org_id, pool in PhoneNumber.objects.values_list('organization_id', 'pool'):
    key = ('main' if org_id == main.pk else 'stock' if org_id == stock_id else 'customer', pool or '(empty)')
    pools[key] = pools.get(key, 0) + 1
print('     TFNs by account and pool:', ', '.join(f'{a}/{p}={n}' for (a, p), n in sorted(pools.items())))
line(not any(a == 'customer' and p == 'main' for (a, p) in pools),
     'Point 5: no customer account holds a Main (ours) TFN')
line(not any(a == 'main' and p != 'main' for (a, p) in pools),
     'Point 5: every one of our TFNs is in the Main pool')
line(not any((a == 'stock') != (p == 'stock') for (a, p) in pools),
     'Point 5: the stock holds only numbers for sale, and only the stock does (CH-665)')

# ── Points 3 and 2/4: as each customer login ────────────────────────────────
ours = set()
ours |= {f'"{n}"' for n in PhoneNumber.objects.filter(organization=main).values_list('number', flat=True)}
for model in (Campaign, Buyer, Publisher):
    ours |= {f'"{n}"' for n in model.objects.filter(organization=main).values_list('name', flat=True) if n and len(n) >= 3}

OWNER_ONLY = ['/api/owner/ping', '/api/owner/organizations', '/api/owner/carrier-costs',
              '/api/owner/company-profit', '/api/owner/routing-cost', '/api/owner/expenses',
              '/api/kyc/', '/api/white-label/', '/api/ai-audit/status', '/api/ai-audit/calls']
THEIR_PAGES = ['/api/analytics/dashboard', '/api/analytics/calls', '/api/campaigns/', '/api/buyers/',
               '/api/publishers/', '/api/numbers/', '/api/stats/', '/api/destinations/stats/']

for org in customers:
    user = User.objects.filter(organization=org, is_active=True).order_by('date_joined').first()
    if user is None:
        print(f'     {org.slug}: no active login, skipped')
        continue
    c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}', HTTP_HOST='avortyx.io')
    refused = [p for p in OWNER_ONLY if c.get(p).status_code == 403]
    line(len(refused) == len(OWNER_ONLY),
         f'Point 3 [{org.slug}]: owner-only sections refused {len(refused)}/{len(OWNER_ONLY)}'
         + ('' if len(refused) == len(OWNER_ONLY) else f' - NOT refused: {sorted(set(OWNER_ONLY) - set(refused))}'))
    leaks, errors = [], []
    for p in THEIR_PAGES:
        r = c.get(p)
        if r.status_code >= 500:
            errors.append(f'{p}={r.status_code}')
            continue
        body = r.content.decode(errors='ignore')
        found = [x for x in ours if x in body]
        if found:
            leaks.append(f'{p}: {", ".join(sorted(found)[:5])}')
    line(not leaks and not errors,
         f'Points 2/4 [{org.slug}]: {len(THEIR_PAGES)} pages opened as {user.email}; '
         f'our TFNs/campaigns/buyers/publishers seen: {len(leaks)}'
         + (f' - {"; ".join(leaks)}' if leaks else '') + (f' - errors: {errors}' if errors else ''))

print(f'\nRESULT: {"ALL OK" if bad == 0 else f"{bad} FAIL"}')
