"""CH-250: Reports read calls the way Revo does.

Sir, 24 Oct:
  * the chart and the Total Calls ring have three colours: Converted (blue),
    Not Converted (yellow: answered, did not convert) and No Answer (red:
    missed AND did not convert). "In revo red candle only comes once it's
    dropped under non conversion missed."
  * a missed call that converted (destination paid on Incoming) reads
    "Missed With Conversion" in Status, and only a call that did not convert
    has a Fail Reason.
  * Payout is each call's own payout - the tracking number's (Yahoo Native $5),
    not the campaign's.

Run:  python manage.py test routing.test_report_buckets
"""
import csv
import io
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.db.models import Sum
from django.test import Client, TestCase, override_settings

from accounts.models import Organization, User
from analytics.models import CallRecord
from analytics.services import AnalyticsService
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
LINE = '+18667404516'


def no_filters(**kw):
    base = dict(date_from=None, date_to=None, campaign_id=None, buyer_id=None, publisher_id=None,
                status=None, is_qualified=None, is_converted=None, is_duplicate=None, is_spam=None,
                destination=None, timezone='UTC', granularity='day')
    base.update(kw)
    return SimpleNamespace(**base)


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class ReportBucketTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='a', email='a@x.io', organization=self.org,
                                         role='admin', is_active=True)
        # Campaign pays the publisher $1; the tracking number pays $5.
        self.campaign = Campaign.objects.create(organization=self.org, name='C-02',
                                                revenue_amount=Decimal('1.00'), payout_amount=Decimal('1.00'),
                                                min_call_duration=0, duplicate_handling='normal')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other', campaign=self.campaign,
                                   status='active', payout_per_call=Decimal('5.00'))
        self.buyer = Buyer.objects.create(organization=self.org, name='Q04')
        Destination.objects.create(organization=self.org, buyer=self.buyer, name='Q04|4516', tfn=LINE, enabled=True)
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        self.row = RuleDestination.objects.create(rule=rule, destination=LINE, buyer=self.buyer, priority=1)
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)
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
                 'recording_url': '', 'dialed_seconds': duration + 5, 'dial_status': dial_status}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        from routing.signals import mirror_call_log
        mirror_call_log(row.id)
        row.refresh_from_db()
        return row, CallRecord.objects.get(id=row.id)

    def series(self):
        rows = AnalyticsService.get_time_series(self.admin, no_filters())
        keys = ('converted', 'not_converted', 'lost')
        return {k: sum(r[k] for r in rows) for k in keys}

    def export(self):
        out = ''.join(AnalyticsService.export_csv(self.admin, no_filters()))
        return list(csv.DictReader(io.StringIO(out)))

    # ── Payout ─────────────────────────────────────────────────────────────

    def test_payout_is_the_numbers_own_not_the_campaigns(self):
        row, rec = self.call(True, 30)
        self.assertEqual(row.publisher_payout, Decimal('5.00'))
        self.assertEqual(rec.payout, Decimal('5.00'))
        self.assertEqual(rec.dynamic_payout, Decimal('5.00'))
        qs = AnalyticsService._base_qs(self.admin, no_filters())
        self.assertEqual(qs.aggregate(t=Sum('dynamic_payout'))['t'], Decimal('5.00'))
        self.assertEqual(Decimal(self.export()[0]['Payout']), Decimal('5.00'))

    def test_a_number_without_its_own_payout_pays_the_campaigns(self):
        PhoneNumber.objects.filter(number=TFN).update(payout_per_call=0)
        row, rec = self.call(True, 30)
        self.assertEqual(rec.dynamic_payout, Decimal('1.00'))

    def test_a_call_that_did_not_convert_pays_nothing(self):
        self.bill(convert_on='connected', revenue=Decimal('3.00'))
        row, rec = self.call(False, 0, dial_status='NOANSWER')
        self.assertEqual(rec.dynamic_payout, 0)

    # ── Sir's two examples: 6 answered, 4 missed ──────────────────────────

    def test_incoming_rule_every_call_is_blue(self):
        self.bill(convert_on='incoming', revenue=Decimal('2.00'))
        for _ in range(6):
            self.call(True, 30)
        for _ in range(4):
            self.call(False, 0, dial_status='NOANSWER')
        self.assertEqual(self.series(), {'converted': 10, 'not_converted': 0, 'lost': 0})

    def test_connected_rule_missed_calls_are_red(self):
        self.bill(convert_on='connected', revenue=Decimal('2.00'))
        for _ in range(6):
            self.call(True, 30)
        for _ in range(4):
            self.call(False, 0, dial_status='NOANSWER')
        self.assertEqual(self.series(), {'converted': 6, 'not_converted': 0, 'lost': 4})

    def test_answered_but_too_short_is_yellow(self):
        self.bill(convert_on='length', convert_seconds=60, revenue=Decimal('2.00'))
        self.call(True, 30)
        self.assertEqual(self.series(), {'converted': 0, 'not_converted': 1, 'lost': 0})

    # ── Status and Fail Reason ─────────────────────────────────────────────

    def test_missed_with_conversion_in_the_export(self):
        self.bill(convert_on='incoming', revenue=Decimal('2.00'))
        self.call(False, 0, dial_status='NOANSWER')
        line = self.export()[0]
        self.assertEqual(line['Status'], 'Missed With Conversion')
        self.assertEqual(line['Fail Reason'], '')

    def test_a_missed_call_that_did_not_convert_keeps_its_status(self):
        self.bill(convert_on='connected', revenue=Decimal('2.00'))
        row, rec = self.call(False, 0, dial_status='NOANSWER')
        CallRecord.objects.filter(id=rec.id).update(block_reason='Call Not Connected')
        line = self.export()[0]
        self.assertEqual(line['Status'], 'no_answer')
        self.assertEqual(line['Fail Reason'], 'Call Not Connected')

    def test_old_calls_get_the_paid_payout(self):
        from django.core.management import call_command
        row, rec = self.call(True, 30)
        CallRecord.objects.filter(id=rec.id).update(payout=Decimal('1.00'))  # as written before CH-250
        out = io.StringIO()
        call_command('resync_call_payouts', stdout=out)
        self.assertIn('1 calls show a payout other than the one paid', out.getvalue())
        self.assertEqual(CallRecord.objects.get(id=rec.id).payout, Decimal('1.00'))
        call_command('resync_call_payouts', '--apply', stdout=out)
        rec.refresh_from_db()
        self.assertEqual(rec.payout, Decimal('5.00'))
        self.assertEqual(rec.profit, rec.revenue - Decimal('5.00'))
