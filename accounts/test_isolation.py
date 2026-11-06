"""Customer accounts point 11 (CH-623): the isolation sweep, run before every
deploy with the rest of the suite.

Three accounts, each with its own campaign, buyer, destination, publisher, TFN
and calls: our main account and two customer accounts, Test A and Test B. They
exist only in the test database (thrown away after the run) - never on the
live system. For each account's login, every page of the API is opened:

  - every list page (no id in its address): no other account's name, number
    or record id may appear in the answer;
  - every page that opens one record: asked for each other account's
    campaign, buyer, publisher, TFN and call, it must not give it out.

And as a control, each login does see its own campaign: a sweep that sees
nothing would prove nothing. Every later customer-accounts point (2, 4, 6-10)
adds its own cases here.

Run:  python manage.py test accounts.test_isolation
"""
import re
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
TODAY = timezone.now().date().isoformat()
DATES = {'date_from': TODAY, 'date_to': TODAY, 'dateFrom': TODAY, 'dateTo': TODAY,
         'start_date': TODAY, 'end_date': TODAY}
SKIP = ('/api/twilio', '/api/accounts/logout')


def api_pages():
    from config.api import api
    paths = api.get_openapi_schema()['paths']
    lists = sorted(p for p, ops in paths.items() if 'get' in ops and '{' not in p and not p.startswith(SKIP))
    one = sorted(p for p, ops in paths.items() if 'get' in ops and p.count('{') == 1 and not p.startswith(SKIP))
    return lists, one


@override_settings(MAIN_ORGANIZATION_SLUG='interns', ALLOWED_HOSTS=['*'], CACHES=CACHE)
class IsolationSweepTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from routing.signals import mirror_call_log
        cls.accounts = {}
        for key, slug, digit in (('main', 'interns', '3'), ('A', 'iso-test-a', '1'), ('B', 'iso-test-b', '2')):
            org = Organization.objects.create(name=f'Isolation {key}', slug=slug)
            tag = f'isoq{key.lower()}x'                       # a word found nowhere else
            campaign = Campaign.objects.create(organization=org, name=f'{tag} campaign')
            buyer = Buyer.objects.create(organization=org, name=f'{tag} buyer', status='active')
            publisher = Publisher.objects.create(organization=org, name=f'{tag} publisher')
            tfn = PhoneNumber.objects.create(organization=org, number=f'+1800555{digit}001',
                                             campaign=campaign, publisher=publisher)
            Destination.objects.create(organization=org, buyer=buyer, name=f'{tag} line',
                                       tfn=f'+1844555{digit}001')
            calls = []
            for i in range(2):
                c = CallLog.objects.create(
                    organization=org, campaign=campaign, buyer=buyer, publisher=publisher,
                    caller_number=f'+1555555{digit}00{i}', called_number=tfn.number,
                    twilio_call_sid=f'CAISO{key}{i}', status=CallLog.Status.COMPLETED, duration=40,
                    destination_number=f'+1844555{digit}001')
                mirror_call_log(str(c.id))                    # its Reports copy, as live
                calls.append(c)
            admin = User.objects.create(username=f'iso-{key}', email=f'iso-{key}@x.io', organization=org,
                                        role='admin', is_active=True)
            cls.accounts[key] = {
                'admin': admin,
                'words': [f'{tag} campaign', f'{tag} buyer', f'{tag} publisher', f'{tag} line'],
                'numbers': [f'800555{digit}001', f'844555{digit}001'] + [f'555555{digit}00{i}' for i in range(2)],
                'ids': {'campaign': str(campaign.id), 'buyer': str(buyer.id), 'publisher': str(publisher.id),
                        'number': str(tfn.id), 'call': str(calls[0].id)},
            }

    def client_for(self, key):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.accounts[key]["admin"])}',
                      raise_request_exception=False)

    @staticmethod
    def body(r):
        """A page's answer as text. A streamed answer (a live event stream, a
        file download) is not read: an event stream never ends."""
        if getattr(r, 'streaming', False):
            return ''
        return r.content.decode('utf-8', 'replace')

    def others_in(self, key, text):
        """What of the other accounts' appears in a page's text."""
        found = []
        for other, acc in self.accounts.items():
            if other == key:
                continue
            found += [f'{other}: name "{w}"' for w in acc['words'] if w in text]
            digits = re.sub(r'\D', '', text)
            found += [f'{other}: number ...{n[-4:]}' for n in acc['numbers'] if n in text or n in digits]
            found += [f'{other}: {kind} id' for kind, i in acc['ids'].items() if i in text]
        return found

    def test_each_account_sees_only_its_own(self):
        lists, one = api_pages()
        self.assertGreater(len(lists), 50)
        problems = []
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
            for key in self.accounts:
                api = self.client_for(key)
                # Control: its own campaign is there.
                own = self.body(api.get('/api/campaigns/'))
                self.assertIn(f'isoq{key.lower()}x campaign', own, f'{key} does not see its own campaign')
                for path in lists:
                    r = api.get(path, DATES)
                    if r.status_code == 200:
                        for f in self.others_in(key, self.body(r)):
                            problems.append(f'{key} login, {path}: {f}')
                for path in one:
                    for other, acc in self.accounts.items():
                        if other == key:
                            continue
                        for kind, rid in acc['ids'].items():
                            r = api.get(re.sub(r'\{[^}]+\}', rid, path))
                            if r.status_code != 200:
                                continue
                            # A page may repeat back what it was asked for (the
                            # caller-profile number, the DNI snippet's pool id)
                            # without giving anything out: the id asked for is
                            # taken out, then nothing else of the other
                            # account may be in the answer.
                            for f in self.others_in(key, self.body(r).replace(rid, '')):
                                problems.append(f'{key} login, {path} asked for {other}\'s {kind}: {f}')
        self.assertEqual(problems, [], '\n' + '\n'.join(problems[:60]))
