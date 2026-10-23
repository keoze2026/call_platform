"""Several publishers on one campaign: each sees only their own (CH-238).

Sir, 24 Oct: "soon we'll have 5-10 publishers under a single campaign ... make
sure each publisher can only see their own assigned TFNs. They shouldn't be
able to see the other publishers or their TFNs in the same campaign."

Two publishers share one campaign, each with a number, callers and calls.
Logged in as the first, every GET endpoint of the API is called, and nothing
of the second - name, number, callers - may come back. Their record and their
number opened by id answer "not found". And the campaign's counters are the
publisher's own calls: counted for the whole campaign they read the other
publishers' call volume.

Run:  python manage.py test publishers.test_shared_campaign_privacy
"""
import re
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
# Everything that would identify the other publisher.
OTHER = ('Bravo Pub', '8885550202', '5550002222')


def body_of(response) -> str:
    raw = b''.join(response.streaming_content) if getattr(response, 'streaming', False) else response.content
    return raw.decode('utf-8', 'replace')


@override_settings(CACHES=CACHE)
class SharedCampaignPrivacyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Org', slug='org')
        self.campaign = Campaign.objects.create(organization=self.org, name='Shared')
        self.mine = Publisher.objects.create(organization=self.org, name='Alpha Pub')
        self.other = Publisher.objects.create(organization=self.org, name='Bravo Pub')
        self.my_number = self.number('+18885550101', self.mine)
        self.other_number = self.number('+18885550202', self.other)
        self.calls(self.mine, self.my_number, '+15550001111', 'a')
        self.calls(self.other, self.other_number, '+15550002222', 'b')
        self.login = User.objects.create(username='alpha', email='alpha@x.io', organization=self.org,
                                         role='publisher', publisher=self.mine, is_active=True)
        self.admin = User.objects.create(username='adm', email='adm@x.io', organization=self.org,
                                         role='admin', is_active=True)

    def number(self, digits, publisher):
        return PhoneNumber.objects.create(organization=self.org, number=digits, vendor='Other',
                                          campaign=self.campaign, publisher=publisher, status='active')

    def calls(self, publisher, number, caller, tag):
        now = timezone.now()
        for status, ended in (('completed', now), ('in_progress', None)):
            CallLog.objects.create(organization=self.org, campaign=self.campaign, publisher=publisher,
                                   caller_number=caller, called_number=number.number,
                                   destination_number='+18667404516', twilio_call_sid=f'{tag}-{status}',
                                   status=status, ended_at=ended)
        CallRecord.objects.create(organization=self.org, campaign=self.campaign, campaign_name='Shared',
                                  publisher_id=publisher.id, publisher_name=publisher.name,
                                  caller_number=caller, called_number=number.number,
                                  status='completed', payout=5, revenue=20, started_at=now)

    def client_for(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def get(self, client, url):
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
            return client.get(url)

    def test_no_endpoint_shows_the_other_publisher(self):
        from config.api import api

        client = self.client_for(self.login)
        ids = {'campaign_id': str(self.campaign.id), 'publisher_id': str(self.other.id),
               'number_id': str(self.other_number.id)}
        today = timezone.now().date().isoformat()
        answered, leaks = 0, []
        for path, methods in api.get_openapi_schema()['paths'].items():
            if 'get' not in methods:
                continue
            names = re.findall(r'{(\w+)}', path)
            if any(n not in ids for n in names):
                continue
            url = path
            for n in names:
                url = url.replace('{%s}' % n, ids[n])
            response = self.get(client, f'{url}?start_date={today}&end_date={today}')
            if response.status_code != 200:
                continue
            answered += 1
            found = [m for m in OTHER if m in body_of(response)]
            if found:
                leaks.append(f'{path}: {found}')
        self.assertGreater(answered, 30, 'the sweep reached too few endpoints to mean anything')
        self.assertEqual(leaks, [])

    def test_the_sweep_would_see_a_leak(self):
        # The same sweep as an admin finds the other publisher: proof that an
        # empty result above means hidden, not unreachable.
        body = body_of(self.get(self.client_for(self.admin), '/api/numbers/'))
        self.assertIn('8885550202', body)

    def test_their_own_numbers_are_listed(self):
        body = body_of(self.get(self.client_for(self.login), '/api/numbers/'))
        self.assertIn('8885550101', body)
        self.assertNotIn('8885550202', body)

    def test_the_other_publisher_and_number_by_id_are_not_found(self):
        client = self.client_for(self.login)
        self.assertEqual(self.get(client, f'/api/publishers/{self.other.id}').status_code, 404)
        self.assertEqual(self.get(client, f'/api/numbers/{self.other_number.id}').status_code, 404)

    def test_campaign_counters_are_their_own_calls(self):
        client = self.client_for(self.login)
        listed = self.get(client, '/api/campaigns/').json()['items'][0]
        detail = self.get(client, f'/api/campaigns/{self.campaign.id}').json()
        for row in (listed, detail):
            self.assertEqual(row['live_calls'], 1)
            self.assertEqual(row['calls_today'], 1)
            self.assertEqual(row['calls_global'], 1)

    def test_staff_still_see_the_whole_campaign(self):
        detail = self.get(self.client_for(self.admin), f'/api/campaigns/{self.campaign.id}').json()
        self.assertEqual(detail['live_calls'], 2)
        self.assertEqual(detail['calls_today'], 2)
