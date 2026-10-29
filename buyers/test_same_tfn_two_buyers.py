"""CH-392: one TFN can be live under several buyers - once per buyer.

The campaign team (29 Oct): "We are unable to add similar TFNs under different
buyers, we have to pause one in order to add or send calls to another."

Covered here:
  - the same number live on two buyers is accepted (create, switch on, bulk);
  - the same buyer twice on one number is still refused, in words;
  - each buyer's row counts only the calls routed to that buyer;
  - "Different" by buyer never sends a repeat caller back to a TFN he already
    reached, even through the other buyer holding the same number.

Run:  python manage.py test buyers.test_same_tfn_two_buyers
"""
import json
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SHARED = '+18443131353'
OTHER = '+18667404516'
SECRET = 'test-secret'
TFN = '+18888910343'
CALLER = '+13072281061'


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class SameTfnTwoBuyersTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.user = User.objects.create(username='m', email='m@x.io', organization=self.org,
                                        role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}')
        self.bhs = Buyer.objects.create(organization=self.org, name='BHS')
        self.xyz = Buyer.objects.create(organization=self.org, name='XYZ')
        self.bhs_line = Destination.objects.create(organization=self.org, buyer=self.bhs,
                                                   name='BHS', tfn=SHARED, enabled=True)

    def create(self, buyer, name, tfn=SHARED, enabled=True):
        return self.api.post('/api/destinations/', json.dumps({
            'buyer_id': str(buyer.id), 'name': name, 'tfn': tfn, 'enabled': enabled,
        }), content_type='application/json')

    def test_the_same_number_live_on_another_buyer_is_accepted(self):
        r = self.create(self.xyz, 'XYZ')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Destination.objects.filter(tfn=SHARED, enabled=True).count(), 2)

    def test_the_same_buyer_twice_on_one_number_is_still_refused(self):
        r = self.create(self.bhs, 'BHS again')
        self.assertEqual(r.status_code, 400)
        self.assertIn('already live on BHS', r.json()['detail'])

    def test_switching_on_and_bulk_play_accept_another_buyer(self):
        line = Destination.objects.create(organization=self.org, buyer=self.xyz,
                                          name='XYZ', tfn=SHARED, enabled=False)
        r = self.api.patch(f'/api/destinations/{line.id}/', {'enabled': True},
                           content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        Destination.objects.filter(pk=line.pk).update(enabled=False)
        r = self.api.post('/api/destinations/bulk-enable', {'ids': [str(line.id)], 'enabled': True},
                          content_type='application/json')
        self.assertEqual(r.json()['updated'], [str(line.id)], r.content)

    def test_bulk_play_refuses_the_same_buyer_twice(self):
        line = Destination.objects.create(organization=self.org, buyer=self.bhs,
                                          name='BHS 2', tfn=SHARED, enabled=False)
        r = self.api.post('/api/destinations/bulk-enable', {'ids': [str(line.id)], 'enabled': True},
                          content_type='application/json')
        self.assertEqual(r.json()['failed_count'], 1)

    def test_each_buyer_counts_only_its_own_calls(self):
        xyz_line = Destination.objects.create(organization=self.org, buyer=self.xyz,
                                              name='XYZ', tfn=SHARED, enabled=True)
        for i, buyer in enumerate([self.bhs, self.xyz, self.xyz]):
            CallLog.objects.create(organization=self.org, buyer=buyer, caller_number='+1555000000%d' % i,
                                   called_number=TFN, twilio_call_sid=f'live-{i}',
                                   destination_number=SHARED, status='in_progress')
        CallLog.objects.create(organization=self.org, buyer=self.xyz, caller_number='+15550001111',
                               called_number=TFN, twilio_call_sid='done', destination_number=SHARED,
                               status='completed')
        rows = {d['id']: d for d in self.api.get('/api/destinations/?page_size=50').json()['items']}
        bhs, xyz = rows[str(self.bhs_line.id)], rows[str(xyz_line.id)]
        self.assertEqual((bhs['live_calls'], bhs['daily_calls']), (1, 1))
        self.assertEqual((xyz['live_calls'], xyz['daily_calls']), (2, 3))
        # One row on its own (no page counts) agrees.
        one = self.api.get(f'/api/destinations/{xyz_line.id}/').json()
        self.assertEqual((one['live_calls'], one['daily_calls']), (2, 3))
        stats = self.api.get('/api/destinations/stats/').json()
        self.assertEqual(stats['active_live'], 3)


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class DifferentByBuyerNeverSameTfnTests(TestCase):
    """Two buyers on one number: excluding the buyer alone would send the
    repeat caller to the same line through the other one."""

    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(
            organization=self.org, name='C-05', duplicate_handling='different',
            duplicate_direction='buyer', duplicate_call_block_hours=24)
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        self.bhs = Buyer.objects.create(organization=self.org, name='BHS')
        self.xyz = Buyer.objects.create(organization=self.org, name='XYZ')
        self.abc = Buyer.objects.create(organization=self.org, name='ABC')
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        for i, (buyer, number) in enumerate(((self.bhs, SHARED), (self.xyz, SHARED), (self.abc, OTHER)), 1):
            Destination.objects.create(organization=self.org, buyer=buyer, name=buyer.name,
                                       tfn=number, enabled=True)
            RuleDestination.objects.create(rule=rule, destination=number, buyer=buyer, priority=i)
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def dial(self):
        self.n += 1
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'):
            r = Client().post('/api/twilio/asterisk/route/',
                              data=json.dumps({'caller_number': CALLER, 'called_number': TFN,
                                               'asterisk_call_id': f'sid-{self.n}'}),
                              content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        return CallLog.objects.get(twilio_call_sid=f'sid-{self.n}')

    def test_a_repeat_caller_skips_the_other_buyer_on_the_same_tfn(self):
        first = self.dial()
        self.assertEqual((first.destination_number, first.buyer_id), (SHARED, self.bhs.id))
        CallLog.objects.filter(pk=first.pk).update(status='no_answer', block_reason='Not Connected')
        second = self.dial()
        self.assertEqual((second.destination_number, second.buyer_id), (OTHER, self.abc.id))
