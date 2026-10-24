"""CH-255: what a user workspace pays per call (Sir, 24 Oct).

- Every ANSWERED call pays its minutes, converted or not ($0.045 a minute,
  each call rounded up to the whole minute), to the hundredth of a cent -
  not rounded to the cent on every call.
- The VoIP Shield fee is paid on every call the Shield checked, answered,
  missed or refused.
"""
import json
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import Organization
from billing.models import BillingAccount, Transaction
from billing.services import BillingService
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
LINE = '+18667404516'


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False,
    ENFORCE_CALL_BALANCE=True, CHARGE_COMPLETED_CALLS=True,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class UserChargeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='PDSO', slug='pdso')
        # Converts only after 60 seconds, so a 30-second call is answered but not converted.
        self.campaign = Campaign.objects.create(organization=self.org, name='C-02',
                                                revenue_amount=Decimal('1.00'), payout_amount=Decimal('1.00'),
                                                min_call_duration=60, duplicate_handling='normal')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        buyer = Buyer.objects.create(organization=self.org, name='Q04')
        Destination.objects.create(organization=self.org, buyer=buyer, name='Q04|4516', tfn=LINE, enabled=True)
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        RuleDestination.objects.create(rule=rule, destination=LINE, buyer=buyer, priority=1)
        self.account = BillingAccount.objects.create(
            organization=self.org, status=BillingAccount.Status.ACTIVE, balance=Decimal('100'),
            per_minute_rate=Decimal('0.045'), markup_percent=0, recording_fee_per_minute=0,
            voip_shield_fee_per_call=Decimal('0.01'), rejected_call_fee=Decimal('0.015'))
        q = mock.patch('celery.app.task.Task.apply_async')
        q.start()
        self.addCleanup(q.stop)
        self.n = 0

    def call(self, answered, seconds, voip=False):
        self.n += 1
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'), \
                mock.patch('tasks.retire_used_number.delay'), self.captureOnCommitCallbacks(execute=True):
            c = Client()
            c.post('/api/twilio/asterisk/route/', data=json.dumps(
                {'caller_number': f'+1555000{self.n:04d}', 'called_number': TFN,
                 'asterisk_call_id': f'sid-{self.n}'}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
            row = CallLog.objects.get(twilio_call_sid=f'sid-{self.n}')
            if voip:
                CallLog.objects.filter(pk=row.pk).update(ipqs_checked=True)
            c.post('/api/twilio/asterisk/call-ended/', data=json.dumps(
                {'call_log_id': str(row.id), 'duration': seconds, 'answered': answered,
                 'recording_url': '', 'dialed_seconds': seconds + 5,
                 'dial_status': 'ANSWER' if answered else 'NOANSWER'}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        row.refresh_from_db()
        return row

    def charged(self, row):
        return sum((t.amount for t in Transaction.objects.filter(call_sid=row.twilio_call_sid)), Decimal('0'))

    def test_an_answered_call_that_did_not_convert_still_pays_its_minutes(self):
        row = self.call(True, 30)
        self.assertFalse(row.revenue)                  # did not convert
        self.assertEqual(self.charged(row), Decimal('0.045'))  # 1 minute x $0.045, not rounded to $0.05

    def test_minutes_round_up_at_0045(self):
        self.assertEqual(BillingService.call_cost(self.org, 61), Decimal('0.09'))
        self.assertEqual(BillingService.call_cost(self.org, 1), Decimal('0.045'))
        self.assertEqual(BillingService.call_cost(self.org, 600), Decimal('0.45'))

    def test_a_missed_call_pays_nothing_unless_the_shield_checked_it(self):
        self.assertEqual(self.charged(self.call(False, 0)), 0)
        self.assertEqual(self.charged(self.call(False, 0, voip=True)), Decimal('0.01'))

    def test_an_answered_checked_call_pays_minutes_and_the_shield(self):
        self.assertEqual(self.charged(self.call(True, 120, voip=True)), Decimal('0.10'))  # 0.09 + 0.01

    def test_a_refused_checked_call_pays_the_rejected_fee_and_the_shield(self):
        from tasks import charge_rejected_call
        row = CallLog.objects.create(organization=self.org, campaign=self.campaign, caller_number='+15559990000',
                                     called_number=TFN, twilio_call_sid='refused-1', status='failed',
                                     block_reason='Callerid Blacklisted', ipqs_checked=True)
        charge_rejected_call(str(row.id))
        self.assertEqual(Transaction.objects.get(call_sid='refused-1').amount, Decimal('0.025'))  # 0.015 + 0.01
        self.assertEqual(Transaction.objects.get(call_sid='refused-1').description, 'Rejected call fee + VoIP Shield')

    def test_a_refusal_without_the_rejected_fee_still_pays_the_shield(self):
        from tasks import charge_rejected_call
        row = CallLog.objects.create(organization=self.org, campaign=self.campaign, caller_number='+15559990001',
                                     called_number=TFN, twilio_call_sid='dup-1', status='no_answer',
                                     block_reason='Duplicate call', ipqs_checked=True)
        charge_rejected_call(str(row.id))
        self.assertEqual(Transaction.objects.get(call_sid='dup-1').amount, Decimal('0.01'))

    def test_new_workspaces_start_at_0045(self):
        org = Organization.objects.create(name='New', slug='new')
        self.assertEqual(BillingAccount.objects.create(organization=org).per_minute_rate, Decimal('0.0450'))
