"""Read-only check (Server B), customer accounts point 9: does anything a
customer account gets - pages, DOWNLOADS (CSV / Excel), and the emails and
SMS sent to it - carry our internal names or numbers?

    cd /opt/call_platform && docker compose exec -T web python manage.py shell < scripts/point9_check.py

Same as tenant_check.py v2, plus: our team (logins' names and email
addresses) and our account's own name are looked for too; files are read as
text and searched; notification emails/SMS stored for customer accounts
(notification_logs) are searched; pages answering with counting ids (1, 2,
3...) are listed.

For every active login of a workspace other than the main account (texora on
6 Nov), every GET page of the API without an id in its path is called as that
login, inside the server (a token made in memory, never printed), with
today's date range, in a transaction that is rolled back: nothing is saved.
Every answer is scanned for the main account's campaign, buyer and publisher
names, our TFNs, buyers' destination numbers and the callers of our last
2,000 calls. Prints counts and where, never the values in full.
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
from accounts.tenancy import main_organization_id
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher
from routing.models import CallLog

TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()
HOST = next((h for h in settings.ALLOWED_HOSTS if h and '*' not in h and not h.startswith('.')), 'localhost')
d10 = lambda s: re.sub(r'\D', '', str(s or ''))[-10:]
norm = lambda s: re.sub(r'\s+', ' ', str(s or '')).strip().lower()

main = main_organization_id()
if main is None:
    raise SystemExit('No main account (MAIN_ORGANIZATION_SLUG) - nothing to compare with.')

names, numbers = {}, {}
for n in Campaign.objects.filter(organization_id=main).values_list('name', flat=True):
    if len(norm(n)) >= 3:
        names[norm(n)] = 'our campaign'
for n in Buyer.objects.filter(organization_id=main).values_list('name', flat=True):
    if len(norm(n)) >= 3:
        names[norm(n)] = 'our buyer'
for n in Publisher.objects.filter(organization_id=main).values_list('name', flat=True):
    if len(norm(n)) >= 3:
        names[norm(n)] = 'our publisher'
for n in PhoneNumber.objects.filter(organization_id=main).values_list('number', flat=True):
    if len(d10(n)) == 10:
        numbers[d10(n)] = 'our TFN'
for n in Destination.objects.filter(buyer__organization_id=main).values_list('tfn', flat=True):
    if len(d10(n)) == 10:
        numbers.setdefault(d10(n), 'our buyer destination')
for n in (CallLog.objects.filter(organization_id=main).order_by('-created_at')
          .values_list('caller_number', flat=True)[:2000]):
    if len(d10(n)) == 10:
        numbers.setdefault(d10(n), 'our caller')
our_ids = {str(i) for i in Campaign.objects.filter(organization_id=main).values_list('id', flat=True)}
our_ids |= {str(i) for i in Buyer.objects.filter(organization_id=main).values_list('id', flat=True)}
our_ids |= {str(i) for i in Publisher.objects.filter(organization_id=main).values_list('id', flat=True)}


from accounts.models import Organization  # noqa: E402
our_org = Organization.objects.filter(id=main).first()
if our_org and len(norm(our_org.name)) >= 3:
    names[norm(our_org.name)] = 'our account name'
for first, last, email in User.objects.filter(organization_id=main).values_list('first_name', 'last_name', 'email'):
    full = norm(f'{first} {last}')
    if len(full) >= 5:
        names.setdefault(full, 'our team member')
    if email and len(email) >= 6:
        names.setdefault(norm(email), 'our team email')
# For text files: only names long enough not to match by chance.
long_names = {n: k for n, k in names.items() if len(n) >= 5}

logins = list(User.objects.filter(is_active=True, organization__isnull=False)
              .exclude(organization_id=main).select_related('organization'))


def get_paths():
    from config.api import api
    return sorted({p for p, ops in api.get_openapi_schema()['paths'].items() if 'get' in ops and '{' not in p})


def int_ids(node, where=''):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == 'id' and isinstance(v, int) and not isinstance(v, bool):
                yield where
            yield from int_ids(v, f'{where}.{k}')
    elif isinstance(node, list):
        for v in node[:50]:
            yield from int_ids(v, where)


def walk(node, where=''):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f'{where}.{k}')
    elif isinstance(node, list):
        for i, v in enumerate(node[:500]):
            yield from walk(v, f'{where}[{i}]')
    elif isinstance(node, str):
        yield where, node


paths = get_paths()
counting_ids = defaultdict(int)
print(f'Main account: {len(names)} names, {len(numbers)} numbers to look for · '
      f'customer logins: {len(logins)} · pages: {len(paths)} · day {TODAY}')
total = 0
for user in logins:
    from rest_framework_simplejwt.tokens import AccessToken
    client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}', HTTP_HOST=HOST)
    found, codes = defaultdict(list), defaultdict(int)
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
                found[path].append(('page raised', type(e).__name__, ''))
                continue
            codes[r.status_code] += 1
            if r.status_code != 200:
                continue
            ctype = r.get('Content-Type', '')
            if 'json' not in ctype:
                # A download (CSV, Excel, PDF...): read it as text and search.
                raw = b''.join(r.streaming_content) if getattr(r, 'streaming', False) else r.content
                if 'spreadsheet' in ctype or raw[:2] == b'PK':
                    import io, zipfile
                    try:
                        z = zipfile.ZipFile(io.BytesIO(raw))
                        raw = b' '.join(z.read(n) for n in z.namelist() if n.endswith('.xml'))
                    except Exception:  # noqa: BLE001
                        pass
                text = norm(raw.decode('utf-8', 'replace'))
                for n, kind in long_names.items():
                    if n in text:
                        found[path].append((kind + ' (in a download)', n[:3] + '...', ctype[:30]))
                for digits in set(re.findall(r'(?<!\d)1?(\d{10})(?!\d)', re.sub(r'[ ()+.-]', '', text))):
                    if digits in numbers:
                        found[path].append((numbers[digits] + ' (in a download)', '...' + digits[-4:], ctype[:30]))
                continue
            try:
                body = json.loads(r.content)
            except ValueError:
                continue
            for _ in int_ids(body):
                counting_ids[path] += 1
            for where, val in walk(body):
                if where.rsplit('.', 1)[-1] in ('category', 'slug', 'icon', 'type', 'kind'):
                    continue
                v = norm(val)
                if v in names:
                    found[path].append((names[v], val[:3] + '...', where))
                elif val in our_ids:
                    found[path].append(('our record id', val[:8] + '...', where))
                else:
                    digits = d10(val)
                    if len(digits) == 10 and digits in numbers and len(re.sub(r'\D', '', val)) <= 12:
                        found[path].append((numbers[digits], '...' + digits[-4:], where))
    n = sum(len(v) for v in found.values())
    total += n
    print(f'\n== {user.organization.slug} login ({user.email[:3]}***, role {user.role}): '
          f'answers {dict(codes)} -> {n} items of ours on {len(found)} pages')
    for path, items in sorted(found.items(), key=lambda kv: -len(kv[1]))[:30]:
        kinds = defaultdict(int)
        for what, _, _ in items:
            kinds[what] += 1
        what, val, where = items[0]
        print(f'   {path}: {len(items)} ({", ".join(f"{k} x{c}" for k, c in kinds.items())}); '
              f'e.g. {what} "{val}" at {where[:60]}')

# v2: pages that open ONE record by its id (/api/campaigns/{id} ...), asked
# for one of OUR records. A customer login must never get it.
from django.urls import get_resolver  # noqa: E402
probe_ids = {
    'campaign': str(Campaign.objects.filter(organization_id=main).values_list('id', flat=True).first() or ''),
    'buyer': str(Buyer.objects.filter(organization_id=main).values_list('id', flat=True).first() or ''),
    'publisher': str(Publisher.objects.filter(organization_id=main).values_list('id', flat=True).first() or ''),
    'number': str(PhoneNumber.objects.filter(organization_id=main).values_list('id', flat=True).first() or ''),
    'call': str(CallLog.objects.filter(organization_id=main).order_by('-created_at')
                .values_list('id', flat=True).first() or ''),
}
probe_ids = {k: v for k, v in probe_ids.items() if v}
from config.api import api  # noqa: E402
one_id = sorted(p for p, ops in api.get_openapi_schema()['paths'].items()
                if 'get' in ops and p.count('{') == 1 and not p.startswith('/api/twilio'))
print(f'\nPages that open one record by id: {len(one_id)} · our records tried on each: {", ".join(probe_ids)}')
for user in logins:
    from rest_framework_simplejwt.tokens import AccessToken
    client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}', HTTP_HOST=HOST)
    got, codes = [], defaultdict(int)
    with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
        for path in one_id:
            for kind, rid in probe_ids.items():
                url = re.sub(r'\{[^}]+\}', rid, path)
                try:
                    with transaction.atomic():
                        r = client.get(url, secure=True)
                        transaction.set_rollback(True)
                except Exception as e:  # noqa: BLE001
                    codes['error'] += 1
                    continue
                codes[r.status_code] += 1
                if r.status_code == 200 and 'json' in r.get('Content-Type', '') and rid.encode() in r.content:
                    got.append((path, kind))
    total += len(got)
    print(f'== {user.organization.slug} login: answers {dict(codes)} -> {len(got)} of our records opened')
    for path, kind in got[:30]:
        print(f'   {path}: our {kind} opened')
print(f'\nTOTAL items of the main account seen by customer logins: {total}'
      + ('  - fully separated.' if total == 0 else ''))

print(f'\nPages answering with counting ids (1, 2, 3...): {len(counting_ids)}')
for path, n in sorted(counting_ids.items()):
    print(f'   {path}: {n}')

# Emails and SMS stored for customer accounts.
from notifications.models import NotificationLog  # noqa: E402
sent = NotificationLog.objects.exclude(organization_id=main)
mail_found = defaultdict(int)
for subject, body in sent.values_list('subject', 'body').iterator():
    text = norm(f'{subject} {body}')
    for n, kind in long_names.items():
        if n in text:
            mail_found[kind] += 1
    for digits in set(re.findall(r'(?<!\d)1?(\d{10})(?!\d)', re.sub(r'[ ()+.-]', '', text))):
        if digits in numbers:
            mail_found[numbers[digits]] += 1
print(f'\nEmails/SMS stored for customer accounts: {sent.count()} -> items of ours: '
      f'{dict(mail_found) or 0}')
total += sum(mail_found.values())
print(f'\nPOINT 9 TOTAL (pages, downloads, emails): {total} items of ours' + ('  - none.' if total == 0 else ''))
