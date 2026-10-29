"""CH-396: a destination's Hourly / Daily / Monthly / Global caps are applied.

Sir, 29 Oct: "Daily cap system is not properly functioning, you can see calls
went extra than the daily cap limit set" (R48 13/12, Q07 20/10, Q17 6/3).

Run:  python manage.py test buyers.test_destination_caps
"""
import json
from datetime import timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from buyers.caps import buyer_cap_hit, destination_cap_hit
from buyers.destination import Destination
from buyers.models import Buyer, BuyerCap
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
A, B = '+18554707586', '+18663990119'
ET = ZoneInfo('America/New_York')


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class DestinationCapTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-03')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        self.r48 = Buyer.objects.create(organization=self.org, name='R48')
        self.q28 = Buyer.objects.create(organization=self.org, name='Q28')
        self.line_a = Destination.objects.create(organization=self.org, buyer=self.r48, name='R48|7586',
                                                 tfn=A, enabled=True, daily_cap=2)
        self.line_b = Destination.objects.create(organization=self.org, buyer=self.q28, name='Q28|0119',
                                                 tfn=B, enabled=True)
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        # Listed without the + on purpose: the line is found by its digits.
        RuleDestination.objects.create(rule=rule, destination=A.lstrip('+'), buyer=self.r48, priority=1)
        RuleDestination.objects.create(rule=rule, destination=B, buyer=self.q28, priority=2)
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def dial(self):
        self.n += 1
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'):
            r = Client().post('/api/twilio/asterisk/route/',
                              data=json.dumps({'caller_number': f'+1307228{self.n:04d}', 'called_number': TFN,
                                               'asterisk_call_id': f'sid-{self.n}'}),
                              content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        return CallLog.objects.get(twilio_call_sid=f'sid-{self.n}')

    def past(self, number, buyer, **extra):
        self.n += 1
        return CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=buyer,
                                      caller_number=f'+1307999{self.n:04d}', called_number=TFN,
                                      twilio_call_sid=f'old-{self.n}', destination_number=number,
                                      status='completed', **extra)

    def test_a_full_daily_cap_sends_the_call_to_the_next_line(self):
        self.assertEqual(self.dial().destination_number, A)
        self.assertEqual(self.dial().destination_number, A)
        third = self.dial()
        self.assertEqual(third.destination_number, B)
        self.assertIn('daily cap 2/2', json.dumps(third.routing_trace))
        self.assertEqual(destination_cap_hit(self.line_a), 'daily cap 2/2')

    def test_the_only_line_full_is_a_missed_call_on_it_and_the_count_stays_at_the_cap(self):
        Destination.objects.filter(pk=self.line_b.pk).update(enabled=False)
        self.dial()
        self.dial()
        third = self.dial()
        self.assertEqual(third.block_reason, 'No Destination')
        self.assertEqual(third.destination_number.lstrip('+'), A.lstrip('+'))  # missed on that line
        # The refusal stamped on the line does not count: the page reads 2 / 2.
        from buyers.destinations_api import format_destination
        self.line_a.refresh_from_db()
        self.assertEqual(format_destination(self.line_a)['daily_calls'], 2)

    def test_refusals_and_hidden_retries_do_not_count(self):
        self.past(A, self.r48, block_reason='No Destination')
        self.past(A, self.r48, retry_hidden=True)
        self.past(A, self.r48)
        self.assertIsNone(destination_cap_hit(self.line_a))
        self.past(A, self.r48)
        self.assertEqual(destination_cap_hit(self.line_a), 'daily cap 2/2')

    def test_the_day_starts_at_midnight_eastern(self):
        local_midnight = timezone.localtime(timezone.now(), ET).replace(hour=0, minute=0, second=0, microsecond=0)
        for _ in range(2):
            c = self.past(A, self.r48)
            CallLog.objects.filter(pk=c.pk).update(created_at=local_midnight - timedelta(minutes=5))
        self.assertIsNone(destination_cap_hit(self.line_a))

    def test_hourly_monthly_and_global_caps(self):
        Destination.objects.filter(pk=self.line_a.pk).update(daily_cap=0, hourly_cap=1)
        self.line_a.refresh_from_db()
        self.past(A, self.r48)
        self.assertEqual(destination_cap_hit(self.line_a), 'hourly cap 1/1')
        Destination.objects.filter(pk=self.line_a.pk).update(hourly_cap=0, global_cap=3)
        self.line_a.refresh_from_db()
        self.assertIsNone(destination_cap_hit(self.line_a))
        self.past(A, self.r48)
        self.past(A, self.r48)
        self.assertEqual(destination_cap_hit(self.line_a), 'global cap 3/3')

    def test_no_cap_is_no_cap(self):
        Destination.objects.filter(pk=self.line_a.pk).update(daily_cap=0)
        self.line_a.refresh_from_db()
        for _ in range(5):
            self.past(A, self.r48)
        self.assertIsNone(destination_cap_hit(self.line_a))

    def test_another_buyer_on_the_same_number_has_its_own_count(self):
        other = Buyer.objects.create(organization=self.org, name='BHS')
        for _ in range(2):
            self.past(A, other)
        self.assertIsNone(destination_cap_hit(self.line_a))

    def test_cc_and_switched_off_apply_to_a_tfn_listed_without_the_plus(self):
        Destination.objects.filter(pk=self.line_a.pk).update(daily_cap=0, concurrency_cap=1)
        self.assertEqual(self.dial().destination_number, A)      # left live (in progress)
        self.assertEqual(self.dial().destination_number, B)      # A at CC 1/1
        CallLog.objects.filter(destination_number=A).update(status='completed', ended_at=timezone.now())
        Destination.objects.filter(pk=self.line_a.pk).update(enabled=False)
        self.assertEqual(self.dial().destination_number, B)      # A switched off

    def test_rotation_skips_a_tfn_at_its_cap(self):
        second = Destination.objects.create(organization=self.org, buyer=self.r48, name='R48|2',
                                            tfn='+18554700000', enabled=True)
        for _ in range(2):
            self.past(A, self.r48)
        for _ in range(4):
            self.assertEqual(RoutingEngine.pick_live_destination(self.r48).id, second.id)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class BuyerCapDayTests(TestCase):
    def test_buyer_daily_cap_counts_the_eastern_day_and_only_calls_sent(self):
        org = Organization.objects.create(name='Avortyx', slug='avx')
        buyer = Buyer.objects.create(organization=org, name='CDM')
        cap = BuyerCap.objects.create(buyer=buyer, max_calls_daily=2)
        midnight = timezone.localtime(timezone.now(), ET).replace(hour=0, minute=0, second=0, microsecond=0)

        def call(i, **extra):
            return CallLog.objects.create(organization=org, buyer=buyer, caller_number=f'+1555000{i:04d}',
                                          called_number=TFN, twilio_call_sid=f'b-{i}', destination_number=A,
                                          status='completed', **extra)
        # Yesterday evening Eastern: UTC's "today" when after 8 PM; not today.
        y = call(1)
        CallLog.objects.filter(pk=y.pk).update(created_at=midnight - timedelta(hours=1))
        call(2, block_reason='No Destination')
        call(3)
        self.assertIsNone(buyer_cap_hit(buyer, cap))
        call(4)
        self.assertEqual(buyer_cap_hit(buyer, cap), 'daily cap 2/2')
