"""A number blocked on one publisher is refused on that publisher's numbers
only (Sir, 20 Oct: "give an option to block numbers under publishers").

Run:  python manage.py test spam_protection.test_publisher_blocks
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from publishers.models import Publisher
from routing.engine import RoutingEngine
from routing.models import RoutingRule, RuleDestination
from spam_protection.models import Blacklist
from spam_protection.services import SpamProtectionService

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CALLER = '+16283585660'
DEST = '+15550000001'


def post(client, data):
    with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
        return client.post('/api/spam/blacklist', data=json.dumps(data), content_type='application/json')


@override_settings(CACHES=CACHE)
class PublisherBlockRoutingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Org', slug='org')
        self.pub_a = Publisher.objects.create(organization=self.org, name='PUB A')
        self.pub_b = Publisher.objects.create(organization=self.org, name='PUB B')
        self.campaign = Campaign.objects.create(organization=self.org, name='C')
        rule = RoutingRule.objects.create(
            organization=self.org, campaign=self.campaign, name='R',
            rule_type='priority', priority=1)
        RuleDestination.objects.create(rule=rule, destination=DEST, priority=1)
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def block(self, publisher, number=CALLER, **extra):
        return Blacklist.objects.create(
            organization_id=self.org.id, phone_number=number, publisher=publisher,
            reason='manual', **extra)

    def route(self, publisher):
        return RoutingEngine.route_call(str(self.campaign.id), {
            'caller_number': CALLER, 'twilio_call_sid': 'x',
            'publisher_id': str(publisher.id),
        })

    def test_an_unblocked_caller_routes(self):
        self.assertEqual(self.route(self.pub_a)['destination'], DEST)

    def test_blocked_on_a_publisher_is_refused_on_that_publisher(self):
        self.block(self.pub_a)
        decision = self.route(self.pub_a)
        self.assertIsNone(decision['destination'])
        self.assertEqual(decision['error'], RoutingEngine.BLOCKED_CALLER_LABEL)

    def test_the_same_caller_still_routes_on_another_publisher(self):
        self.block(self.pub_a)
        self.assertEqual(self.route(self.pub_b)['destination'], DEST)

    def test_it_is_not_an_organization_wide_block(self):
        self.block(self.pub_a)
        self.assertFalse(SpamProtectionService.is_blacklisted(
            CALLER, str(self.org.id), str(self.campaign.id)))

    def test_it_matches_however_the_carrier_writes_the_number(self):
        self.block(self.pub_a, number='6283585660')
        self.assertIsNone(self.route(self.pub_a)['destination'])

    def test_an_inactive_or_expired_block_lets_the_call_through(self):
        self.block(self.pub_a, is_active=False)
        self.assertEqual(self.route(self.pub_a)['destination'], DEST)
        Blacklist.objects.all().delete()
        self.block(self.pub_a, expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.route(self.pub_a)['destination'], DEST)

    def test_a_call_with_no_publisher_is_untouched(self):
        self.block(self.pub_a)
        decision = RoutingEngine.route_call(str(self.campaign.id), {
            'caller_number': CALLER, 'twilio_call_sid': 'x'})
        self.assertEqual(decision['destination'], DEST)


@override_settings(CACHES=CACHE)
class PublisherBlockApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Org', slug='org')
        self.pub_a = Publisher.objects.create(organization=self.org, name='PUB A')
        self.pub_b = Publisher.objects.create(organization=self.org, name='PUB B')
        self.admin = User.objects.create(
            username='adm', email='adm@x.io', organization=self.org, role='admin', is_active=True)
        self.admin_client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')

    def member(self, publisher, switch_on):
        publisher.permissions = {'block_numbers': switch_on}
        publisher.save()
        user = User.objects.create(
            username=f'm-{publisher.name}-{switch_on}', email=f'm{publisher.name}{switch_on}@x.io'.replace(' ', ''),
            organization=self.org, role='publisher', publisher=publisher, is_active=True)
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def get(self, client, **query):
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
            r = client.get('/api/spam/blacklist', query)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['items']

    def test_admin_blocks_a_number_on_one_publisher(self):
        r = post(self.admin_client, {'phone_number': '(628) 358-5660', 'publisher_id': str(self.pub_a.id)})
        self.assertEqual(r.status_code, 201, r.content)
        row = Blacklist.objects.get()
        self.assertEqual(row.publisher_id, self.pub_a.id)
        self.assertEqual(row.phone_number, '6283585660')
        self.assertIsNone(row.campaign_id)

    def test_an_unknown_publisher_is_refused(self):
        other = Organization.objects.create(name='Other', slug='other')
        stranger = Publisher.objects.create(organization=other, name='X')
        r = post(self.admin_client, {'phone_number': CALLER, 'publisher_id': str(stranger.id)})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(Blacklist.objects.count(), 0)

    def test_the_workspace_list_leaves_publisher_rows_out(self):
        Blacklist.objects.create(organization_id=self.org.id, phone_number='111', publisher=self.pub_a)
        Blacklist.objects.create(organization_id=self.org.id, phone_number='222')
        self.assertEqual([i['phone_number'] for i in self.get(self.admin_client)], ['222'])

    def test_one_publishers_list_shows_only_its_own(self):
        Blacklist.objects.create(organization_id=self.org.id, phone_number='111', publisher=self.pub_a)
        Blacklist.objects.create(organization_id=self.org.id, phone_number='333', publisher=self.pub_b)
        rows = self.get(self.admin_client, publisher_id=str(self.pub_a.id))
        self.assertEqual([i['phone_number'] for i in rows], ['111'])
        self.assertEqual(rows[0]['publisher_id'], str(self.pub_a.id))

    def test_a_publisher_login_with_the_switch_on_blocks_on_its_own_numbers(self):
        client = self.member(self.pub_a, True)
        # Even if it names another publisher, the row lands on its own.
        r = post(client, {'phone_number': CALLER, 'publisher_id': str(self.pub_b.id)})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Blacklist.objects.get().publisher_id, self.pub_a.id)

    def test_a_publisher_login_with_the_switch_off_cannot_block(self):
        client = self.member(self.pub_a, False)
        self.assertEqual(post(client, {'phone_number': CALLER}).status_code, 403)
        self.assertEqual(Blacklist.objects.count(), 0)

    def test_a_publisher_login_sees_and_removes_only_its_own(self):
        mine = Blacklist.objects.create(organization_id=self.org.id, phone_number='111', publisher=self.pub_a)
        theirs = Blacklist.objects.create(organization_id=self.org.id, phone_number='333', publisher=self.pub_b)
        client = self.member(self.pub_a, True)
        self.assertEqual([i['phone_number'] for i in self.get(client)], ['111'])
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
            self.assertEqual(client.delete(f'/api/spam/blacklist/{theirs.id}').status_code, 403)
            self.assertEqual(client.delete(f'/api/spam/blacklist/{mine.id}').status_code, 200)
        self.assertEqual(list(Blacklist.objects.values_list('phone_number', flat=True)), ['333'])
