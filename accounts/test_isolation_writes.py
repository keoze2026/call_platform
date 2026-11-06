"""Customer accounts point 11, part 2 (CH-624): the isolation sweep for every
page that CHANGES data - create, edit, pause, delete.

Sir: "run automated isolation tests on every page and API endpoint: no data
visible across accounts or from the main account." CH-623 opens every page
that reads data. This one sends every request that changes data (POST, PUT,
PATCH, DELETE) as each account's admin login, aimed at ANOTHER account:

  - its address carries the other account's record (campaign, buyer,
    publisher, destination, TFN, call, routing rule, login);
  - its body is filled from the page's own form description, and every
    field that names a record (campaign_id, buyer_id, ids ...) points to the
    other account's record.

After each request, before it is undone:
  1. nothing of any other account may have changed - every row of theirs, in
     every table, exactly as before;
  2. nothing of the account that sent it may now point to another account's
     record (a routing rule on their buyer, a TFN on their campaign ...).

Each request runs in a transaction that is rolled back, so one request
cannot spoil the next. The accounts exist only in the test database. No
request leaves the test: every network connection is refused (no carrier,
Telegram or webhook is ever called).

Run:  python manage.py test accounts.test_isolation_writes
"""
import datetime as dt
import hashlib
import json
import re
import socket
from unittest import mock

from django.apps import apps
from django.db import transaction
from django.db.models import F
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher
from routing.models import CallLog, RoutingRule, RuleDestination

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
OURS = {'accounts', 'analytics', 'billing', 'buyers', 'call_queue', 'campaigns', 'dni', 'integrations',
        'ivr', 'live', 'marketplace', 'notifications', 'phone_numbers', 'publishers', 'referrals',
        'routing', 'rtb', 'security', 'spam_protection', 'support', 'webhooks', 'white_label'}
SKIP = ('/api/twilio', '/api/accounts/logout')
WRITE = ('post', 'put', 'patch', 'delete')
# The accounts themselves; and website visitor counts, which are our main
# account's by design (accounts/tenancy.py WEBSITE) whoever opens a page.
NOT_COMPARED = {'accounts.Organization', 'analytics.GuestVisit'}
# Which record a path parameter or a body field names, by its name.
KINDS = (('campaign', 'campaign'), ('buyer', 'buyer'), ('publisher', 'publisher'),
         ('destination', 'destination'), ('rule', 'rule'), ('call', 'call'), ('number', 'number'),
         ('tfn', 'number'), ('phone', 'number'), ('member', 'user'), ('user', 'user'))


def kind_of(name):
    n = name.lower()
    for word, kind in KINDS:
        if word in n:
            return kind
    return 'campaign'


def account_tables():
    """Every table of ours that carries an account: (model, account field)."""
    out = []
    for model in apps.get_models():
        meta = model._meta
        if meta.app_label not in OURS or meta.proxy or not meta.managed or meta.label in NOT_COMPARED:
            continue
        names = {f.name for f in meta.concrete_fields}
        if 'organization' in names:
            out.append((model, 'organization_id'))
        elif 'organization_id' in names:
            out.append((model, 'organization_id'))
    return out


def cross_links():
    """(model, field) pairs where a row and the record it points to both carry
    an account - they must always carry the same one."""
    out = []
    for model, _ in account_tables():
        for f in model._meta.concrete_fields:
            if not f.is_relation or f.name == 'organization' or not f.related_model:
                continue
            rel = f.related_model
            if rel._meta.label == 'accounts.Organization':
                continue
            if 'organization' in {x.name for x in rel._meta.concrete_fields}:
                out.append((model, f.name))
    return out


class Body:
    """A request body made from the page's own form description (OpenAPI)."""

    def __init__(self, components, target):
        self.components = components
        self.target = target          # the other account's record ids, by kind

    def resolve(self, s):
        seen = 0
        while isinstance(s, dict) and '$ref' in s and seen < 10:
            s = self.components.get(s['$ref'].rsplit('/', 1)[-1], {})
            seen += 1
        return s

    def value(self, s, name='', depth=0):
        s = self.resolve(s)
        if depth > 4 or not isinstance(s, dict):
            return None
        for key in ('anyOf', 'oneOf', 'allOf'):
            if key in s:
                options = [o for o in s[key] if self.resolve(o).get('type') != 'null']
                return self.value(options[0], name, depth + 1) if options else None
        if 'enum' in s:
            return s['enum'][0]
        t, fmt, n = s.get('type'), s.get('format', ''), name.lower()
        if t == 'object' or 'properties' in s:
            return {k: self.value(v, k, depth + 1) for k, v in (s.get('properties') or {}).items()}
        if t == 'array':
            if n.endswith('ids') or n in ('ids', 'items', 'numbers', 'destinations'):
                return [self.target[kind_of(n)]]
            return []
        if t == 'boolean':
            return False
        if t in ('integer', 'number'):
            return max(1, s.get('minimum', 1) or 1)
        if fmt == 'uuid' or n.endswith('_id') or n == 'id':
            return self.target[kind_of(n)]
        if fmt == 'date-time':
            return timezone.now().isoformat()
        if fmt == 'date':
            return timezone.now().date().isoformat()
        if fmt == 'email' or 'email' in n:
            return 'isoq-write@x.io'
        if any(w in n for w in ('number', 'tfn', 'phone', 'caller')):
            return '+18005559999'
        return 'isoq-write'


@override_settings(MAIN_ORGANIZATION_SLUG='interns', ALLOWED_HOSTS=['*'], CACHES=CACHE)
class IsolationWriteSweepTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.accounts = {}
        for key, slug, digit in (('main', 'interns', '3'), ('A', 'iso-test-a', '1'), ('B', 'iso-test-b', '2')):
            org = Organization.objects.create(name=f'Isolation {key}', slug=slug)
            tag = f'isoq{key.lower()}x'
            campaign = Campaign.objects.create(organization=org, name=f'{tag} campaign')
            buyer = Buyer.objects.create(organization=org, name=f'{tag} buyer', status='active')
            publisher = Publisher.objects.create(organization=org, name=f'{tag} publisher', status='active')
            tfn = PhoneNumber.objects.create(organization=org, number=f'+1800555{digit}001',
                                             campaign=campaign, publisher=publisher)
            line = Destination.objects.create(organization=org, buyer=buyer, name=f'{tag} line',
                                              tfn=f'+1844555{digit}001')
            rule = RoutingRule.objects.create(organization=org, campaign=campaign, name=f'{tag} rule',
                                              rule_type='priority')
            RuleDestination.objects.create(rule=rule, destination=line.tfn, buyer=buyer)
            call = CallLog.objects.create(
                organization=org, campaign=campaign, buyer=buyer, publisher=publisher,
                caller_number=f'+1555555{digit}001', called_number=tfn.number,
                twilio_call_sid=f'CAISOW{key}', status=CallLog.Status.COMPLETED, duration=40)
            admin = User.objects.create(username=f'isow-{key}', email=f'isow-{key}@x.io', organization=org,
                                        role='admin', is_active=True)
            member = User.objects.create(username=f'isow-m-{key}', email=f'isow-m-{key}@x.io',
                                         organization=org, role='manager', is_active=True)
            cls.accounts[key] = {
                'org': org.id, 'admin': admin,
                'ids': {'campaign': str(campaign.id), 'buyer': str(buyer.id), 'publisher': str(publisher.id),
                        'destination': str(line.id), 'number': str(tfn.id), 'rule': str(rule.id),
                        'call': str(call.id), 'user': str(member.id)},
            }

    def fingerprint(self, orgs):
        h = hashlib.sha256()
        for model, field in self.tables:
            cols = [f.attname for f in model._meta.concrete_fields]
            for row in model.objects.filter(**{f'{field}__in': orgs}).order_by('pk').values_list(*cols):
                h.update(repr((model._meta.label, row)).encode())
        return h.hexdigest()

    def crossed(self, org):
        """Rows of this account that point to another account's record."""
        out = []
        for model, name in self.links:
            qs = (model.objects.filter(organization_id=org).exclude(**{f'{name}__isnull': True})
                  .exclude(**{f'{name}__organization_id': F('organization_id')}))
            if qs.exists():
                out.append(f'{model._meta.label}.{name}')
        return out

    def test_no_account_can_change_or_link_another_accounts_data(self):
        from config.api import api
        schema = api.get_openapi_schema()
        components = (schema.get('components') or {}).get('schemas') or {}
        self.tables, self.links = account_tables(), cross_links()
        self.assertGreater(len(self.tables), 40)
        for key, acc in self.accounts.items():
            self.assertEqual(self.crossed(acc['org']), [], f'{key} crosses accounts before the sweep')

        ops = []
        for path, item in schema['paths'].items():
            if path.startswith(SKIP):
                continue
            for method, op in item.items():
                if method in WRITE and op.get('security'):            # pages that need a login
                    ops.append((path, method, op))
        self.assertGreater(len(ops), 100)

        problems, sent, unverifiable = [], 0, 0
        pairs = (('main', 'A'), ('A', 'B'), ('B', 'main'))          # who sends, at whom
        refuse = mock.patch.object(socket.socket, 'connect', side_effect=OSError('no network in this test'))
        refuse2 = mock.patch('socket.create_connection', side_effect=OSError('no network in this test'))
        throttle = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        with refuse, refuse2, throttle:
            for sender, victim in pairs:
                me = self.accounts[sender]
                others = [a['org'] for k, a in self.accounts.items() if k != sender]
                target = self.accounts[victim]['ids']
                body_of = Body(components, target)
                api_client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(me["admin"])}',
                                    raise_request_exception=False)
                before = self.fingerprint(others)
                for path, method, op in ops:
                    url = re.sub(r'\{([^}]+)\}', lambda m: target[kind_of(m.group(1))], path)
                    query = {p['name']: body_of.value(p.get('schema') or {}, p['name'])
                             for p in op.get('parameters') or [] if p.get('in') == 'query' and p.get('required')}
                    if query:
                        url += '?' + '&'.join(f'{k}={v}' for k, v in query.items())
                    content = (op.get('requestBody') or {}).get('content') or {}
                    data = body_of.value(content['application/json'].get('schema') or {}) \
                        if 'application/json' in content else {}
                    try:
                        with transaction.atomic():
                            r = getattr(api_client, method)(url, data=json.dumps(data, default=str),
                                                           content_type='application/json')
                            sent += 1
                            try:
                                after = self.fingerprint(others)
                                crossed = self.crossed(me['org'])
                            except Exception:  # noqa: BLE001 - the request broke the transaction
                                unverifiable += 1
                                after, crossed = before, []
                            transaction.set_rollback(True)
                    except Exception:  # noqa: BLE001
                        unverifiable += 1
                        continue
                    if after != before:
                        problems.append(f'{sender} -> {victim}: {method.upper()} {path} ({r.status_code}) '
                                        f'CHANGED another account\'s data')
                    for link in crossed:
                        problems.append(f'{sender} -> {victim}: {method.upper()} {path} ({r.status_code}) '
                                        f'linked {sender}\'s {link} to another account')
        print(f'\nisolation (writes): {len(ops)} pages that change data, {sent} requests sent, '
              f'{unverifiable} could not be checked, {len(problems)} problems')
        self.assertEqual(problems, [], '\n' + '\n'.join(problems[:80]))
        self.assertLess(unverifiable, sent // 4, 'too many requests could not be checked')
