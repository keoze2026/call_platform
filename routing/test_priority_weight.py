"""CH-249: Standard routing honours priority, then weight, as on Revo.

Sir, 24 Oct: "if any TFN is set on priority, calls should land there first;
priority and weight should work perfectly".

Run:  python manage.py test routing.test_priority_weight
"""
import json
from collections import Counter
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
A, B, C = '+18443122521', '+18332779804', '+18443125984'


@override_settings(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PriorityWeightTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-02', duplicate_handling='normal')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        self.rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                               name='R', rule_type='priority', priority=1)
        self.buyers = {}
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def line(self, number, priority=1, weight=100, buyer='Q04', cap=0):
        b = self.buyers.get(buyer) or Buyer.objects.create(organization=self.org, name=buyer)
        self.buyers[buyer] = b
        Destination.objects.create(organization=self.org, buyer=b, name=f'{buyer}|{number[-4:]}',
                                   tfn=number, enabled=True, concurrency_cap=cap)
        return RuleDestination.objects.create(rule=self.rule, destination=number, buyer=b,
                                              priority=priority, weight=weight)

    def routed(self, n=1):
        out = Counter()
        for _ in range(n):
            self.n += 1
            d = RoutingEngine.route_call(str(self.campaign.id), {'caller_number': f'+1555{self.n:07d}',
                                                                  'twilio_call_sid': f's{self.n}'})
            out[d.get('destination')] += 1
        return out

    def test_priority_1_takes_every_call(self):
        self.line(B, priority=2)
        self.line(A, priority=1)
        self.assertEqual(self.routed(30), Counter({A: 30}))

    def test_priority_2_only_when_priority_1_is_off(self):
        self.line(A, priority=1)
        self.line(B, priority=2)
        Destination.objects.filter(tfn=A).update(enabled=False)
        self.assertEqual(self.routed(10), Counter({B: 10}))

    def test_priority_2_only_when_priority_1_is_full(self):
        self.line(A, priority=1, cap=1)
        self.line(B, priority=2)
        CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyers['Q04'],
                               caller_number='+15550009999', called_number=TFN, destination_number=A,
                               twilio_call_sid='live', status='in_progress')
        self.assertEqual(self.routed(10), Counter({B: 10}))

    def test_same_priority_splits_by_equal_weight(self):
        self.line(A)
        self.line(B)
        self.line(C)
        got = self.routed(600)
        self.assertEqual(set(got), {A, B, C})
        for number in (A, B, C):
            self.assertTrue(140 <= got[number] <= 260, got)  # about 200 each

    def test_weight_75_25(self):
        self.line(A, weight=75)
        self.line(B, weight=25)
        got = self.routed(800)
        self.assertTrue(520 <= got[A] <= 680, got)  # about 600

    def test_weight_0_takes_calls_only_when_alone(self):
        self.line(A, weight=100)
        self.line(B, weight=0)
        self.assertEqual(self.routed(50), Counter({A: 50}))
        Destination.objects.filter(tfn=A).update(enabled=False)
        self.assertEqual(self.routed(5), Counter({B: 5}))

    def test_a_full_line_is_skipped_inside_its_priority(self):
        self.line(A, cap=1)
        self.line(B)
        self.line(C, priority=2)
        CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyers['Q04'],
                               caller_number='+15550009999', called_number=TFN, destination_number=A,
                               twilio_call_sid='live', status='in_progress')
        self.assertEqual(self.routed(20), Counter({B: 20}))

    def test_the_dialled_number_is_the_routed_tfn(self):
        # One buyer, a priority 1 and a priority 2 TFN: the call must ring the
        # priority 1 TFN, never be rotated onto the buyer's other one.
        self.line(A, priority=1)
        self.line(B, priority=2)
        dialled = Counter()
        for _ in range(12):
            self.n += 1
            with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'):
                Client().post('/api/twilio/asterisk/route/', data=json.dumps(
                    {'caller_number': f'+1555{self.n:07d}', 'called_number': TFN, 'asterisk_call_id': f'h{self.n}'}),
                    content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
            row = CallLog.objects.get(twilio_call_sid=f'h{self.n}')
            dialled[row.destination_number] += 1
            CallLog.objects.filter(pk=row.pk).update(status='completed')
        self.assertEqual(dialled, Counter({A: 12}))
