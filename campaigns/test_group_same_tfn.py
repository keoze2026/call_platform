"""CH-483: the same TFN under two buyers is two lines in a campaign group.

The campaign team (3 Nov): CRM's +18444974105 (1 CC, 10 caps) could not be
added to the group's campaigns - the number was already there for Q08, and
the group keyed a line by its number alone.

Run:  python manage.py test campaigns.test_group_same_tfn
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from routing.models import RoutingRule, RuleDestination

TFN = '+18444974105'


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class SameTfnTwoBuyersInGroupTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        admin = User.objects.create(username='ops', email='ops@avortyx.io', organization=self.org,
                                    role='admin', is_active=True)
        self.c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        self.q08 = Buyer.objects.create(organization=self.org, name='Q08')
        self.crm = Buyer.objects.create(organization=self.org, name='CRM')
        Destination.objects.create(organization=self.org, buyer=self.q08, name='Q08|4105|', tfn=TFN)
        Destination.objects.create(organization=self.org, buyer=self.crm, name='CRM|4105|', tfn=TFN,
                                   concurrency_cap=1, daily_cap=10)
        self.camps = [Campaign.objects.create(organization=self.org, name=f'C-{i}') for i in (1, 2)]
        for c in self.camps:
            r = RoutingRule.objects.create(organization=self.org, campaign=c, name=f'{c.name} routing',
                                           rule_type='priority', priority=1)
            RuleDestination.objects.create(rule=r, destination=TFN, buyer=self.q08, priority=1, weight=100,
                                           revenue='1.00', convert_on='incoming')
        r = self.post('/api/campaign-groups/', {'name': 'Medicare', 'campaign_ids': [str(c.id) for c in self.camps]})
        self.assertEqual(r.status_code, 201, r.content)
        self.group = r.json()['id']

    def post(self, url, body):
        return self.c.post(url, json.dumps(body), content_type='application/json')

    def lines(self):
        r = self.c.get(f'/api/campaign-groups/{self.group}/destinations')
        self.assertEqual(r.status_code, 200, r.content)
        return {i['buyer_name']: i for i in r.json()['items']}

    def rows(self, buyer):
        return list(RuleDestination.objects.filter(rule__campaign__in=self.camps, buyer=buyer))

    def test_second_buyer_is_added_and_shown_as_its_own_line(self):
        self.assertEqual(set(self.lines()), {'Q08'})
        r = self.post('/api/campaign-groups/apply', {
            'group_ids': [self.group], 'action': 'add', 'number': TFN, 'buyer_id': str(self.crm.id),
            'priority': 5, 'revenue': 2, 'convert_on': 'connected'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['changed'], 2)
        lines = self.lines()
        self.assertEqual(set(lines), {'Q08', 'CRM'})
        self.assertEqual(lines['CRM']['in_count'], 2)
        self.assertEqual(lines['CRM']['priorities'], [5])
        self.assertEqual(lines['Q08']['priorities'], [1])
        self.assertEqual(len(self.rows(self.q08)), 2)

    def test_changing_or_removing_one_buyer_leaves_the_other(self):
        self.post('/api/campaign-groups/apply', {
            'group_ids': [self.group], 'action': 'add', 'number': TFN, 'buyer_id': str(self.crm.id),
            'revenue': 2, 'convert_on': 'connected'})
        r = self.post(f'/api/campaign-groups/{self.group}/line', {
            'number': TFN, 'buyer_id': str(self.crm.id),
            'campaigns': [{'campaign_id': str(c.id), 'included': True, 'priority': 7} for c in self.camps]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual({d.priority for d in self.rows(self.crm)}, {7})
        self.assertEqual({d.priority for d in self.rows(self.q08)}, {1})
        r = self.post('/api/campaign-groups/apply', {
            'group_ids': [self.group], 'action': 'remove', 'number': TFN, 'buyer_id': str(self.crm.id)})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.rows(self.crm), [])
        self.assertEqual(len(self.rows(self.q08)), 2)

    def test_both_buyers_in_one_bulk_add_is_not_called_a_duplicate(self):
        RuleDestination.objects.filter(buyer=self.q08).delete()
        r = self.post('/api/campaign-groups/apply', {
            'group_ids': [self.group], 'action': 'add', 'number': TFN, 'revenue': 1, 'convert_on': 'incoming',
            'numbers': [{'number': TFN, 'buyer_id': str(self.q08.id)}, {'number': TFN, 'buyer_id': str(self.crm.id)}]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(set(self.lines()), {'Q08', 'CRM'})
