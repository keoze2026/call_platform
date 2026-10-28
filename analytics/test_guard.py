"""CH-354: Avortyx Guard - the numbers checker finds what CH-352 would have.

Run:  python manage.py test analytics.test_guard
"""
import io
from datetime import datetime, time as dtime, timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization, User
from analytics import guard
from analytics.models import CallRecord
from billing.models import BillingAccount, Transaction
from campaigns.models import Campaign
from routing.models import CallLog

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=LOCMEM)
class GuardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.acct = BillingAccount.objects.create(
            organization=self.org, status=BillingAccount.Status.ACTIVE, balance=Decimal('100'),
            per_minute_rate=Decimal('0.45'), markup_percent=0)
        User.objects.create(username='ops', email='ops@x.io', organization=self.org, role='admin', is_active=True)
        self.camp = Campaign.objects.create(organization=self.org, name='C-13')
        self.day = (timezone.now().astimezone(guard.ET) - timedelta(days=1)).date()
        self.noon = datetime.combine(self.day, dtime(12), tzinfo=guard.ET)
        self.n = 0

    def call(self, charged='0.90', log='0.90', shown='0.90', seconds=75, report=True):
        """An ended call yesterday at noon ET: its charge, call log and Reports row."""
        self.n += 1
        sid = f'sid-{self.n}'
        c = CallLog.objects.create(organization=self.org, campaign=self.camp, twilio_call_sid=sid,
                                   caller_number='+15550000000', called_number='+18005550100',
                                   status='completed', duration=seconds)
        CallLog.objects.filter(pk=c.pk).update(created_at=self.noon, platform_cost=Decimal(log))
        if charged:
            Transaction.objects.create(organization=self.org, billing_account=self.acct, amount=Decimal(charged),
                                       transaction_type=Transaction.Type.CHARGE,
                                       status=Transaction.Status.COMPLETED, call_sid=sid)
        if report:
            CallRecord.objects.update_or_create(id=c.pk, defaults=dict(
                organization=self.org, twilio_call_sid=sid, caller_number='+15550000000',
                called_number='+18005550100', status='completed', duration_seconds=seconds,
                started_at=self.noon, platform_cost=Decimal(shown), campaign_id=self.camp.id,
                campaign_name='C-13'))
        return c

    def by_key(self, tabs=False):
        return {f.key: f for f in guard.run(self.day, tabs=tabs)}

    def test_all_agree(self):
        self.call()
        self.call(seconds=200, charged='1.35', log='1.35', shown='1.35')
        found = self.by_key(tabs=True)
        self.assertTrue(all(f.ok for f in found.values()), {k: f.lines for k, f in found.items()})

    def test_reports_cost_zero_while_charged_is_caught(self):
        """The CH-352 bug: charged and on the call log, $0 in Reports."""
        self.call(shown='0')
        self.call()
        f = self.by_key()['cost']
        self.assertFalse(f.ok)
        self.assertIn('1 calls: Reports shows $0.00, charged $0.90', f.lines[0])
        self.assertIn('sid-1', f.lines[0])

    def test_call_log_without_its_charge_is_caught(self):
        self.call(log='0', shown='0')
        lines = self.by_key()['cost'].lines
        self.assertEqual(len(lines), 2)
        self.assertTrue(any('call log shows $0.00, charged $0.90' in x for x in lines))

    def test_a_call_missing_from_reports_is_caught(self):
        self.call(report=False)
        f = self.by_key()['calls']
        self.assertIn('1 of 1 ended calls are missing from Reports', f.lines[0])

    def test_a_call_stuck_live_is_caught(self):
        c = self.call()
        CallLog.objects.filter(pk=c.pk).update(status='in_progress')
        self.assertIn('1 calls still live after 2 hours', self.by_key()['stuck'].lines[0])

    def test_a_tfn_without_carrier_is_caught(self):
        from phone_numbers.models import PhoneNumber
        PhoneNumber.objects.create(organization=self.org, number='+18005550199', campaign=self.camp)
        self.assertIn('+18005550199', self.by_key()['tfns'].lines[0])

    def test_a_tab_adding_up_differently_is_caught(self):
        self.call()
        self.call(charged='', log='0', shown='0', seconds=61)          # never charged: 2 min estimate
        self.assertTrue(self.by_key(tabs=True)['tabs'].ok)
        # The pre-CH-352 rule: only the charged part when anything was charged.
        with mock.patch('analytics.services.AnalyticsService._cost',
                        side_effect=lambda row, rate, mk: Decimal(row.get('charged_cost') or 0)):
            f = self.by_key(tabs=True)['tabs']
        self.assertFalse(f.ok)
        self.assertIn('Campaign tab', f.lines[0])
        self.assertIn('C-13: 2 calls $0.90 vs 2 calls $1.80', f.lines[0])

    def test_a_broken_check_never_stops_the_others(self):
        self.call(shown='0')
        with mock.patch('analytics.guard.check_tfns', side_effect=RuntimeError('db away')):
            found = self.by_key()
        self.assertIn('could not check', found['tfns'].lines[0])
        self.assertFalse(found['cost'].ok)

    def test_hourly_sends_a_problem_once_then_back_to_normal(self):
        c = self.call(shown='0')
        sent = []
        at = timezone.now().astimezone(guard.ET).replace(hour=5)
        with mock.patch('analytics.guard.et_day', side_effect=lambda d=None: (self.day, self.noon - timedelta(hours=12),
                                                                               self.noon + timedelta(hours=12))):
            guard.hourly(sent.append, now=at)
            guard.hourly(sent.append, now=at)                       # same problem: not again
            self.assertEqual(len(sent), 1)
            self.assertIn('Reports shows $0.00', sent[0])
            CallRecord.objects.filter(pk=c.pk).update(platform_cost=Decimal('0.90'))
            guard.hourly(sent.append, now=at)
        self.assertIn('back to normal', sent[-1])

    def test_daily_check_goes_once_after_6_am_even_when_all_pass(self):
        self.call()
        sent = []
        at = timezone.now().astimezone(guard.ET).replace(hour=7)
        guard.hourly(sent.append, now=at)
        guard.hourly(sent.append, now=at)
        daily = [s for s in sent if 'daily check' in s]
        self.assertEqual(len(daily), 1)
        self.assertIn('All 5 checks passed', daily[0])

    def test_command_prints_and_changes_nothing(self):
        self.call(shown='0')
        out = io.StringIO()
        call_command('guard_check', '--day', self.day.isoformat(), stdout=out)
        self.assertIn('Reports shows $0.00, charged $0.90', out.getvalue())
        self.assertEqual(CallRecord.objects.get(twilio_call_sid='sid-1').platform_cost, 0)
