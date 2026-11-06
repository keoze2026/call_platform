"""Read-only exposure check (Server B): does any buyer or publisher login receive
what is not its own?  Sir, 6 Nov: "Everything private - main acc, buyers to
buyers things only, publisher to publisher things only. Nothing exposed any
more, do run check once."

    cd /opt/call_platform && docker compose exec -T web python manage.py shell < scripts/exposure_check.py

In the repository since CH-619 (the test accounts.test_partner_guard runs it
on a test database and needs 0 exposures).

For up to 3 buyer logins and 3 publisher logins (active, linked to their buyer /
publisher), every GET page of the API without an id in its path is called as
that login, inside the server (a token made in memory, never printed), with
today's date range. Every answer is scanned for:
  buyer login     - another buyer's name, any publisher, any campaign, any of
                    our TFNs (called numbers), another buyer's destination number,
                    a money field we never give buyers (payout, profit, cost...)
  publisher login - any buyer, any destination (buyer) number, another
                    publisher, revenue / profit / cost
Everything runs in a transaction that is rolled back: nothing is saved. Rate
limits are skipped for these requests only, so no real login is slowed.
"""
import datetime as dt
import json
import re
from collections import defaultdict
from unittest import mock

from django.conf import settings
from django.db import transaction
from django.test import Client

from accounts.models import User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher

try:
    from accounts import rls
    _sys = rls.system
except Exception:  # noqa: BLE001  (before CH-580)
    from contextlib import nullcontext as _sys

TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()
HOST = next((h for h in settings.ALLOWED_HOSTS if h and '*' not in h and not h.startswith('.')), 'localhost')
MONEY_BUYER = {'payout', 'profit', 'cost', 'total_payout', 'total_profit', 'total_cost', 'publisher_payout',
               'dynamic_payout', 'dynamic_profit', 'platform_cost', 'charged_cost', 'carrier_cost'}
MONEY_PUBLISHER = {'revenue', 'profit', 'cost', 'total_revenue', 'total_profit', 'total_cost',
                   'dynamic_revenue', 'dynamic_profit', 'platform_cost', 'charged_cost', 'carrier_cost'}
d10 = lambda s: re.sub(r'\D', '', str(s or ''))[-10:]
norm = lambda s: re.sub(r'\s+', ' ', str(s or '')).strip().lower()


def get_paths():
    from config.api import api
    out = []
    for path, ops in api.get_openapi_schema()['paths'].items():
        if 'get' in ops and '{' not in path:
            out.append(path)
    return sorted(set(out))


def walk(node, where=''):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f'{where}.{k}')
            yield ('key', where, k, v)
    elif isinstance(node, list):
        for i, v in enumerate(node[:500]):
            yield from walk(v, f'{where}[{i}]')
    elif isinstance(node, str):
        yield ('str', where, None, node)


with _sys():
    buyers = {str(b.id): b.name for b in Buyer.objects.all()}
    publishers = {str(p.id): p.name for p in Publisher.objects.all()}
    campaigns = {norm(n) for n in Campaign.objects.values_list('name', flat=True) if len(norm(n)) >= 3}
    our_tfns = {d10(n) for n in PhoneNumber.objects.values_list('number', flat=True) if len(d10(n)) == 10}
    dest_by_buyer = defaultdict(set)
    for b, t in Destination.objects.values_list('buyer_id', 'tfn'):
        if len(d10(t)) == 10:
            dest_by_buyer[str(b)].add(d10(t))
    logins = (list(User.objects.filter(role='buyer', is_active=True, buyer__isnull=False)
                   .order_by('-last_login')[:3])
              + list(User.objects.filter(role='publisher', is_active=True, publisher__isnull=False)
                     .order_by('-last_login')[:3]))

paths = get_paths()
print(f'API GET pages without an id: {len(paths)} · logins checked: {len(logins)} · day {TODAY}')
total_leaks = 0
for user in logins:
    from rest_framework_simplejwt.tokens import AccessToken
    role = user.role
    if role == 'buyer':
        own = str(user.buyer_id)
        names = {norm(n): f'buyer {n}' for i, n in buyers.items() if i != own and len(norm(n)) >= 3}
        names.update({norm(n): f'publisher {n}' for n in publishers.values() if len(norm(n)) >= 3})
        names.update({c: f'campaign {c}' for c in campaigns})
        own_names = {norm(buyers.get(own, ''))}
        numbers = {n: 'our TFN (called number)' for n in our_tfns}
        for b, nums in dest_by_buyer.items():
            if b != own:
                numbers.update({n: 'another buyer\'s destination' for n in nums - dest_by_buyer.get(own, set())})
        money = MONEY_BUYER
    else:
        own = str(user.publisher_id)
        names = {norm(n): f'buyer {n}' for n in buyers.values() if len(norm(n)) >= 3}
        names.update({norm(n): f'publisher {n}' for i, n in publishers.items() if i != own and len(norm(n)) >= 3})
        own_names = {norm(publishers.get(own, ''))}
        numbers = {n: 'buyer destination number' for nums in dest_by_buyer.values() for n in nums}
        money = MONEY_PUBLISHER
    for n in own_names:
        names.pop(n, None)
    client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}', HTTP_HOST=HOST)
    leaks = defaultdict(list)
    codes = defaultdict(int)
    with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
        for path in paths:
            if path.startswith(('/api/twilio', '/api/accounts/logout')):
                continue
            q = {'date_from': TODAY, 'date_to': TODAY, 'dateFrom': TODAY, 'dateTo': TODAY,
                 'start_date': TODAY, 'end_date': TODAY}
            try:
                with transaction.atomic():
                    r = client.get(path, q, secure=True)
                    transaction.set_rollback(True)
            except Exception as e:  # noqa: BLE001
                codes['error'] += 1
                leaks[path].append(('page raised', type(e).__name__, ''))
                continue
            codes[r.status_code] += 1
            if r.status_code != 200 or 'json' not in r.get('Content-Type', ''):
                continue
            try:
                body = json.loads(r.content)
            except ValueError:
                continue
            for kind, where, key, val in walk(body):
                if kind == 'key':
                    if key.lower() in money and val not in (None, '', 0, '0', '0.00', 0.0):
                        leaks[path].append((f'money field "{key}"', str(val)[:20], where))
                    continue
                if where.rsplit('.', 1)[-1] in ('category', 'slug', 'icon', 'type', 'kind'):
                    continue                     # catalogue words ("crm"), not names
                v = norm(val)
                if v in names:
                    leaks[path].append((names[v], val[:40], where))
                digits = d10(val)
                if len(digits) == 10 and digits in numbers and len(re.sub(r'\D', '', val)) <= 12:
                    leaks[path].append((numbers[digits], val[:20], where))
    n = sum(len(v) for v in leaks.values())
    total_leaks += n
    label = buyers.get(own) if role == 'buyer' else publishers.get(own)
    print(f'\n== {role} login of {label} ({user.email.split("@")[0][:3]}***): '
          f'{len(paths)} pages, answers {dict(codes)} -> {n} exposures on {len(leaks)} pages')
    for path, items in sorted(leaks.items(), key=lambda kv: -len(kv[1]))[:25]:
        kinds = defaultdict(int)
        for what, _, _ in items:
            kinds[what.split(' ')[0] if what.startswith(('buyer ', 'publisher ', 'campaign ')) else what] += 1
        what, val, where = items[0]
        print(f'   {path}: {len(items)} ({", ".join(f"{k} x{c}" for k, c in kinds.items())}); '
              f'e.g. {what} = "{val}" at {where[:60]}')
print(f'\nTOTAL exposures: {total_leaks}' + ('  - nothing exposed.' if total_leaks == 0 else ''))
