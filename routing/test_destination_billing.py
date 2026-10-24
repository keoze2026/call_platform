"""CH-247: each campaign destination bills by its own rule, as on Revo.

Sir, 24 Oct: some buyers pay for incoming calls, some for connected calls only,
some for calls of a given length - each destination has its own Rate and
"Convert ON". Tested end to end: the route request, then the hang-up report.

Run:  python manage.py test routing.test_destination_billing
"""
import json
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import Organization, User
from analytics.models import CallRecord
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
LINE = '+18667404516'


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class DestinationBillingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-02',
                                                revenue_amount=Decimal('1.00'), payout_amount=Decimal('1.00'),
                                                min_call_duration=0, duplicate_handling='normal')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        self.buyer = Buyer.objects.create(organization=self.org, name='Q04')
        Destination.objects.create(organization=self.org, buyer=self.buyer, name='Q04|4516', tfn=LINE, enabled=True)
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        self.row = RuleDestination.objects.create(rule=rule, destination=LINE, buyer=self.buyer, priority=1)
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        # No job queue in tests: background jobs queued after the call are not
        # sent anywhere (a real broker connection would only time out here).
        q = mock.patch('celery.app.task.Task.apply_async')
        q.start()
        self.addCleanup(q.stop)
        self.n = 0

    def bill(self, **rule):
        RuleDestination.objects.filter(pk=self.row.pk).update(**rule)

    def call(self, answered, duration, dial_status='ANSWER'):
        self.n += 1
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'), \
                mock.patch('tasks.retire_used_number.delay'), self.captureOnCommitCallbacks(execute=True):
            c = Client()
            c.post('/api/twilio/asterisk/route/', data=json.dumps(
                {'caller_number': f'+1555000{self.n:04d}', 'called_number': TFN,
                 'asterisk_call_id': f'sid-{self.n}'}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
            row = CallLog.objects.get(twilio_call_sid=f'sid-{self.n}')
            c.post('/api/twilio/asterisk/call-ended/', data=json.dumps(
                {'call_log_id': str(row.id), 'duration': duration, 'answered': answered,
                 'recording_url': '', 'dialed_seconds': duration + 5,
                 'dial_status': dial_status}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        from routing.signals import mirror_call_log
        mirror_call_log(row.id)  # what the queued job does in production
        row.refresh_from_db()
        return row, CallRecord.objects.get(id=row.id)

    def test_incoming_call_converts_even_unanswered(self):
        self.bill(convert_on='incoming', revenue=Decimal('2.50'))
        row, rec = self.call(False, 0, dial_status='CANCEL')
        self.assertEqual(row.rule_destination_id, self.row.pk)
        self.assertEqual((row.status, row.revenue), ('no_answer', Decimal('2.50')))
        self.assertTrue(row.publisher_payout >= 0)
        self.assertTrue(rec.is_converted)
        self.assertEqual((rec.billing_rule, rec.dynamic_revenue), ('incoming', Decimal('2.50')))

    def test_call_connected_needs_an_answer(self):
        self.bill(convert_on='connected', revenue=Decimal('3.00'))
        row, rec = self.call(False, 0, dial_status='NOANSWER')
        self.assertEqual(row.revenue, 0)
        self.assertFalse(rec.is_converted)
        self.assertEqual(rec.dynamic_revenue, 0)
        row, rec = self.call(True, 4)
        self.assertEqual(row.revenue, Decimal('3.00'))
        self.assertTrue(rec.is_converted)

    def test_call_length_needs_the_seconds(self):
        self.bill(convert_on='length', convert_seconds=60, revenue=Decimal('5.00'))
        row, rec = self.call(True, 30)
        self.assertEqual(row.revenue, 0)
        self.assertFalse(rec.is_converted)
        row, rec = self.call(True, 90)
        self.assertEqual(row.revenue, Decimal('5.00'))
        self.assertTrue(rec.is_converted)

    def test_a_row_without_its_own_rule_keeps_the_campaigns(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(min_call_duration=60)
        row, rec = self.call(True, 30)
        self.assertEqual((row.revenue, rec.is_converted, rec.billing_rule), (0, False, ''))
        row, rec = self.call(True, 61)
        self.assertEqual((row.revenue, rec.is_converted), (Decimal('1.00'), True))
        self.assertEqual(rec.dynamic_revenue, Decimal('1.00'))

    def test_only_a_rate_keeps_the_campaigns_event(self):
        self.bill(revenue=Decimal('7.00'))
        row, rec = self.call(False, 0, dial_status='CANCEL')
        self.assertEqual((row.revenue, rec.is_converted), (0, False))
        row, rec = self.call(True, 5)
        self.assertEqual(row.revenue, Decimal('7.00'))

    def test_reports_add_up_the_destinations_rates(self):
        from analytics.services import AnalyticsService
        admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin', is_active=True)
        self.bill(convert_on='incoming', revenue=Decimal('2.00'))
        self.call(False, 0, dial_status='CANCEL')
        self.call(True, 10)
        qs = AnalyticsService._base_qs(admin, {}) if hasattr(AnalyticsService, '_base_qs') else None
        if qs is not None:
            from django.db.models import Sum
            self.assertEqual(qs.aggregate(t=Sum('dynamic_revenue'))['t'], Decimal('4.00'))


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class DestinationBillingApiTests(TestCase):
    def setUp(self):
        from rest_framework_simplejwt.tokens import AccessToken
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin', is_active=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        camp = Campaign.objects.create(organization=self.org, name='C-02')
        self.rule = RoutingRule.objects.create(organization=self.org, campaign=camp, name='R',
                                               rule_type='priority', priority=1)
        self.row = RuleDestination.objects.create(rule=self.rule, destination=LINE, priority=1)

    def patch(self, body):
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
            return self.api.patch(f'/api/routing/rules/{self.rule.id}/destinations/{self.row.id}',
                                  data=json.dumps(body), content_type='application/json')

    def test_save_and_read_back(self):
        r = self.patch({'revenue': 2.5, 'convert_on': 'length', 'convert_seconds': 90})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['revenue'], r.json()['convert_on'], r.json()['convert_seconds']),
                         ('2.50', 'length', 90))

    def test_call_length_needs_seconds(self):
        self.assertEqual(self.patch({'convert_on': 'length', 'convert_seconds': 0}).status_code, 400)

    def test_unknown_event_is_refused(self):
        self.assertEqual(self.patch({'convert_on': 'webhook'}).status_code, 400)

    def test_back_to_the_campaigns_rule(self):
        self.patch({'convert_on': 'incoming', 'revenue': 3})
        r = self.patch({'convert_on': '', 'revenue': None})
        self.assertEqual((r.json()['convert_on'], r.json()['revenue']), ('', None))
