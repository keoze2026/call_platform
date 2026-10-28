"""CH-255: user billing as Sir set it, 24 Oct.

- A user's call refused for lack of balance is MISSED with the comment
  "Insufficient Funds"; our own portal is never refused for funds.
- A call let through on the credit limit is charged; a call that happened is
  charged even if the balance ran out meanwhile.
- $0.45 a minute: each call rounded up to the whole minute.

Run:  python manage.py test routing.test_user_billing
"""
import json
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import Organization, User
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
class UserBillingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='PDSO', slug='pdso')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-02',
                                                revenue_amount=Decimal('1.00'), payout_amount=Decimal('1.00'),
                                                min_call_duration=0, duplicate_handling='normal')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        buyer = Buyer.objects.create(organization=self.org, name='Q04')
        Destination.objects.create(organization=self.org, buyer=buyer, name='Q04|4516', tfn=LINE, enabled=True)
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        RuleDestination.objects.create(rule=rule, destination=LINE, buyer=buyer, priority=1)
        self.account = BillingAccount.objects.create(
            organization=self.org, status=BillingAccount.Status.ACTIVE, balance=Decimal('0'),
            per_minute_rate=Decimal('0.45'), markup_percent=0, recording_fee_per_minute=0,
            voip_shield_fee_per_call=0, rejected_call_fee=Decimal('0.015'))
        q = mock.patch('celery.app.task.Task.apply_async')
        q.start()
        self.addCleanup(q.stop)
        self.n = 0

    def route(self):
        self.n += 1
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay') as fee, \
                mock.patch('tasks.retire_used_number.delay'), self.captureOnCommitCallbacks(execute=True):
            r = Client().post('/api/twilio/asterisk/route/', data=json.dumps(
                {'caller_number': f'+1555000{self.n:04d}', 'called_number': TFN,
                 'asterisk_call_id': f'sid-{self.n}'}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        return r.json(), CallLog.objects.get(twilio_call_sid=f'sid-{self.n}'), fee

    def end(self, row, seconds):
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.retire_used_number.delay'), \
                self.captureOnCommitCallbacks(execute=True):
            Client().post('/api/twilio/asterisk/call-ended/', data=json.dumps(
                {'call_log_id': str(row.id), 'duration': seconds, 'answered': True,
                 'recording_url': '', 'dialed_seconds': seconds + 5, 'dial_status': 'ANSWER'}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        row.refresh_from_db()
        return row

    def test_no_funds_is_a_missed_call_with_insufficient_funds(self):
        body, row, fee = self.route()
        self.assertEqual(body['action'], 'hangup')
        self.assertEqual((row.status, row.block_reason), ('no_answer', 'Insufficient Funds'))
        # No rejected-call fee on an account that has no funds.
        from tasks import charge_rejected_call
        self.assertEqual(charge_rejected_call(str(row.id)), 'not a refusal')
        self.assertFalse(Transaction.objects.filter(call_sid=row.twilio_call_sid).exists())

    def test_our_portal_is_never_refused_for_funds(self):
        User.objects.create(username='owner', email='o@x.io', organization=self.org,
                            role='admin', is_superuser=True, is_active=True)
        body, row, _ = self.route()
        self.assertNotEqual(body['action'], 'hangup')
        self.assertNotEqual(row.block_reason, 'Insufficient Funds')

    def test_a_call_let_through_on_credit_is_charged(self):
        BillingAccount.objects.filter(pk=self.account.pk).update(credit_limit=Decimal('10'))
        body, row, _ = self.route()
        self.assertNotEqual(body['action'], 'hangup')
        row = self.end(row, 61)                    # 61 s = 2 minutes at $0.45
        self.assertEqual(row.platform_cost, Decimal('0.90'))
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('-0.90'))

    def test_reports_show_the_charge_not_an_estimate(self):
        """CH-352: the Reports copy carries what was charged, even when its
        background copy read the call before the charge was written."""
        from analytics.models import CallRecord
        from routing.signals import mirror_call_log
        BillingAccount.objects.filter(pk=self.account.pk).update(credit_limit=Decimal('10'))
        _, row, _ = self.route()
        early = []

        def copy_now(call_id):          # the job running at once, before the charge
            early.append(call_id)
            mirror_call_log(call_id)

        with mock.patch('tasks.mirror_call_record.delay', side_effect=copy_now), \
                mock.patch('django.db.transaction.on_commit', side_effect=lambda fn, *a, **k: fn()):
            row = self.end(row, 61)
        self.assertTrue(early)
        self.assertEqual(row.platform_cost, Decimal('0.90'))
        self.assertEqual(CallRecord.objects.get(pk=row.pk).platform_cost, Decimal('0.90'))

    def test_a_call_that_happened_is_charged_even_if_the_balance_ran_out(self):
        BillingAccount.objects.filter(pk=self.account.pk).update(balance=Decimal('1.00'))
        body, row, _ = self.route()
        BillingAccount.objects.filter(pk=self.account.pk).update(balance=Decimal('0.10'))  # drained meanwhile
        row = self.end(row, 300)                   # 5 minutes = $2.25
        self.assertEqual(Transaction.objects.filter(call_sid=row.twilio_call_sid).count(), 1)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('-2.15'))

    def test_minutes_round_up_at_the_users_rate(self):
        self.assertEqual(BillingService.call_cost(self.org, 1), Decimal('0.45'))
        self.assertEqual(BillingService.call_cost(self.org, 60), Decimal('0.45'))
        self.assertEqual(BillingService.call_cost(self.org, 61), Decimal('0.90'))
        self.assertEqual(BillingService.call_cost(self.org, 0), Decimal('0.00'))
