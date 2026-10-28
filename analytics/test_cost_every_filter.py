"""CH-352: Cost is the same on every tab and every filter.

Sir, 28 Oct: "Cost is not calculating correctly ... for every filter the cost
should be always correct". Two faults:

  * the hangup charge was written on the call log only, so Reports (which
    read their own copy) estimated most calls instead of showing the charge;
  * a summary row showed only the charged part when any call in it was
    charged, dropping the rest - so Publisher, Campaign and Buyer disagreed
    with the tabs that add up the calls (Dialed, Destination, Date ...).

Run:  python manage.py test analytics.test_cost_every_filter
"""
import io
import uuid
from decimal import Decimal

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from analytics.services import AnalyticsService
from billing.models import BillingAccount, Transaction


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class CostEveryTabTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        BillingAccount.objects.create(
            organization=self.org, status=BillingAccount.Status.ACTIVE, balance=Decimal('0'),
            per_minute_rate=Decimal('0.45'), markup_percent=0, recording_fee_per_minute=0,
            voip_shield_fee_per_call=0, rejected_call_fee=Decimal('0'))
        owner = User.objects.create(username='o', email='o@x.io', organization=self.org,
                                    role='admin', is_superuser=True)
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(owner)}')
        self.day = timezone.now().date().isoformat()
        self.pub = uuid.uuid4()

    def record(self, **kw):
        fields = dict(organization=self.org, caller_number='+15551234567',
                      called_number='+18005550100', status='completed',
                      started_at=timezone.now(), publisher_id=self.pub, publisher_name='Crug',
                      twilio_call_sid=f'sid-{uuid.uuid4().hex[:10]}')
        fields.update(kw)
        return CallRecord.objects.create(**fields)

    def get(self, path):
        r = self.client.get(f'/api/analytics/{path}?date_from={self.day}&date_to={self.day}&timezone=UTC&limit=100')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def per_call_total(self):
        return sum(Decimal(str(c['cost'])) for c in self.get('calls')['items'])

    def test_a_mix_of_charged_and_uncharged_calls_adds_up(self):
        self.record(duration_seconds=75, platform_cost=Decimal('1.15'))   # charged, with fees
        self.record(duration_seconds=61)                                   # not charged: 2 min = 0.90
        self.record(duration_seconds=30)                                   # not charged: 1 min = 0.45
        self.record(status='no_answer', duration_seconds=0)
        self.assertEqual(self.per_call_total(), Decimal('2.50'))
        self.assertEqual(Decimal(str(self.get('publishers')[0]['total_cost'])), Decimal('2.50'))

    def test_dashboard_total_is_the_sum_of_the_calls(self):
        self.record(duration_seconds=75, platform_cost=Decimal('1.15'))
        self.record(duration_seconds=61)
        self.assertEqual(self.per_call_total(), Decimal('2.05'))
        self.assertEqual(AnalyticsService._cost(
            {'charged_cost': Decimal('1.15'), 'uncharged_minutes': 2.0},
            Decimal('0.45'), Decimal('1')), Decimal('2.05'))

    def test_a_tfn_test_call_costs_nothing(self):
        self.record(duration_seconds=40, twilio_call_sid='tfntest-abc123')
        self.record(duration_seconds=75, platform_cost=Decimal('0.90'))
        self.assertEqual(self.per_call_total(), Decimal('0.90'))
        self.assertEqual(Decimal(str(self.get('publishers')[0]['total_cost'])), Decimal('0.90'))

    def test_old_rows_without_the_split_still_work(self):
        rate, mk = Decimal('0.45'), Decimal('1')
        self.assertEqual(AnalyticsService._cost({'charged_cost': Decimal('3'), 'billable_minutes': 9}, rate, mk),
                         Decimal('3.00'))
        self.assertEqual(AnalyticsService._cost({'charged_cost': 0, 'billable_minutes': 2}, rate, mk),
                         Decimal('0.90'))


class SyncCallCostsTests(TestCase):
    """The backfill puts each call's charge on the call log and in Reports."""

    def setUp(self):
        from routing.models import CallLog
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.acct = BillingAccount.objects.create(
            organization=self.org, status=BillingAccount.Status.ACTIVE, balance=Decimal('10'))
        self.call = CallLog.objects.create(
            organization=self.org, twilio_call_sid='sid-sync-1', caller_number='+15551234567',
            called_number='+18005550100', status="completed", duration=75)
        CallLog.objects.filter(pk=self.call.pk).update(platform_cost=Decimal('1.15'))
        # The Reports copy, as the mirror left it before CH-352: Cost 0.
        CallRecord.objects.update_or_create(id=self.call.pk, defaults=dict(
            organization=self.org, twilio_call_sid='sid-sync-1', caller_number='+15551234567',
            called_number='+18005550100', status='completed', duration_seconds=75,
            started_at=timezone.now(), platform_cost=0))
        Transaction.objects.create(
            organization=self.org, billing_account=self.acct, amount=Decimal('1.15'),
            transaction_type=Transaction.Type.CHARGE, status=Transaction.Status.COMPLETED,
            call_sid='sid-sync-1')

    def run_cmd(self, *args):
        out = io.StringIO()
        call_command('sync_call_costs', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        self.assertEqual(CallRecord.objects.get(pk=self.call.pk).platform_cost, 0)
        out = self.run_cmd()
        self.assertIn('1 calls show a Cost other than their charge in Reports', out)
        self.assertIn('Dry run', out)
        self.assertEqual(CallRecord.objects.get(pk=self.call.pk).platform_cost, 0)

    def test_apply_copies_the_charge(self):
        self.run_cmd('--apply')
        self.assertEqual(CallRecord.objects.get(pk=self.call.pk).platform_cost, Decimal('1.15'))
        self.assertIn('0 calls show', self.run_cmd())


class ClearSavedReportsTests(TestCase):
    def test_clears_every_saved_past_day(self):
        from unittest import mock
        from analytics import report_cache
        client = mock.Mock()
        client.scan_iter.return_value = iter([b'callplatform:1:reports:v2:a', b'callplatform:1:reports:v3:b'])
        fake = mock.Mock()
        fake._cache.get_client.return_value = client
        with mock.patch.object(report_cache, 'cache', fake):
            self.assertEqual(report_cache.clear_all(), 2)
        client.scan_iter.assert_called_once_with(match='*:reports:v*', count=1000)
        client.delete.assert_called_once()

    def test_a_cache_without_scan_is_not_an_error(self):
        from analytics import report_cache
        self.assertEqual(report_cache.clear_all(), 0)    # local memory cache in tests
