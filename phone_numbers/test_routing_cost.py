"""CH-186: routing (termination) cost per Eastern day on Owner > Carrier Costs.

Run:  python manage.py test phone_numbers.test_routing_cost
"""
import json
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from phone_numbers.models import RoutingRate
from routing.models import CallLog

ET = ZoneInfo('America/New_York')
URL = '/api/owner/routing-cost'


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RoutingCostTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='interns')
        self.owner = User.objects.create(username='o', email='o@x.io', organization=self.org,
                                         role='admin', is_superuser=True, is_active=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-03')
        RoutingRate.objects.create(key='termination', per_minute_cost='0.0040', monthly_fee='350.00')
        self.n = 0

    def call(self, when, status='completed', duration=60, destination='+18667404516', hidden=False):
        self.n += 1
        row = CallLog.objects.create(
            organization=self.org, campaign=self.campaign, caller_number='+12025550100', called_number='+18774893790',
            destination_number=destination, twilio_call_sid=f'sid-{self.n}', status=status, duration=duration,
            retry_hidden=hidden)
        CallLog.objects.filter(pk=row.pk).update(created_at=when)

    def get(self, **q):
        r = self.api.get(URL, q)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_cost_per_eastern_day_from_exact_seconds(self):
        self.call(datetime(2026, 10, 20, 9, 0, tzinfo=ET), duration=60)
        self.call(datetime(2026, 10, 20, 23, 59, tzinfo=ET), duration=30)
        self.call(datetime(2026, 10, 20, 10, 0, tzinfo=ET), status='no_answer', duration=0)
        self.call(datetime(2026, 10, 21, 0, 1, tzinfo=ET), duration=120)
        data = self.get(start='2026-10-20', end='2026-10-21')
        d20, d21 = data['days']
        self.assertEqual((d20['date'], d20['forwarded'], d20['connected'], d20['seconds']), ('2026-10-20', 3, 2, 90))
        self.assertEqual(d20['cost_exact'], '0.0060')          # 90s = 1.5 min x $0.004
        self.assertEqual((d21['forwarded'], d21['seconds'], d21['cost_exact']), (1, 120, '0.0080'))
        self.assertEqual(data['totals']['seconds'], 210)
        self.assertEqual(data['totals']['cost_exact'], '0.0140')
        self.assertEqual((data['per_minute_cost'], data['monthly_fee']), ('0.0040', '350.00'))

    def test_calls_not_forwarded_to_a_buyer_cost_nothing(self):
        self.call(datetime(2026, 10, 20, 9, 0, tzinfo=ET), duration=600, destination='')
        day = self.get(start='2026-10-20')['days'][0]
        self.assertEqual((day['forwarded'], day['seconds'], day['cost']), (0, 0, '0.00'))

    def test_days_without_calls_still_listed(self):
        data = self.get(start='2026-10-18', end='2026-10-20')
        self.assertEqual([d['date'] for d in data['days']], ['2026-10-18', '2026-10-19', '2026-10-20'])

    def test_defaults_to_yesterday(self):
        with mock.patch('django.utils.timezone.now', return_value=datetime(2026, 10, 21, 15, 0, tzinfo=ET)):
            data = self.get()
        self.assertEqual((data['start'], data['end']), ('2026-10-20', '2026-10-20'))

    def test_owner_sets_the_rate(self):
        r = self.api.patch(URL + '/rate', data=json.dumps({'per_minute_cost': '0.005', 'monthly_fee': '400'}),
                           content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        rate = RoutingRate.objects.get(key='termination')
        self.assertEqual((str(rate.per_minute_cost), str(rate.monthly_fee)), ('0.0050', '400.00'))

    def test_bad_dates_are_refused(self):
        self.assertEqual(self.api.get(URL, {'start': '20-10-2026'}).status_code, 400)
        self.assertEqual(self.api.get(URL, {'start': '2026-10-21', 'end': '2026-10-20'}).status_code, 400)

    def test_only_the_owner(self):
        admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin', is_active=True)
        c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        self.assertEqual(c.get(URL).status_code, 403)

    # ── CH-334 ────────────────────────────────────────────────────────────

    def test_calls_are_counted_as_the_reports_count_them(self):
        # A caller's call the sending platform retried: the hidden tries are
        # not calls of their own, as in every report (CH-215).
        when = datetime(2026, 10, 27, 9, 0, tzinfo=ET)
        self.call(when, status='no_answer', duration=0, hidden=True)
        self.call(when, status='no_answer', duration=0, hidden=True)
        self.call(when, duration=600)
        day = self.get(start='2026-10-27')['days'][0]
        self.assertEqual((day['forwarded'], day['connected'], day['seconds']), (1, 1, 600))

    def test_average_call_and_cost_per_call(self):
        when = datetime(2026, 10, 27, 9, 0, tzinfo=ET)
        self.call(when, duration=540)
        self.call(when, duration=660)
        t = self.get(start='2026-10-27')['totals']
        self.assertEqual(t['aht_seconds'], 600)
        self.assertEqual(t['cost_exact'], '0.0800')          # 20 min x $0.004
        self.assertEqual(t['cost_per_call'], '0.0400')

    def test_rounding_like_the_carrier_bills(self):
        when = datetime(2026, 10, 27, 9, 0, tzinfo=ET)
        self.call(when, duration=61)
        self.call(when, duration=1)
        self.assertEqual(self.get(start='2026-10-27')['totals']['billed_seconds'], 62)   # exact by default
        r = self.api.patch(URL + '/rate', data=json.dumps({'billing_increment': 60}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['billing_increment'], 60)
        t = self.get(start='2026-10-27')['totals']
        self.assertEqual((t['seconds'], t['billed_seconds'], t['minutes']), (62, 180, '3.00'))
        self.assertEqual(t['cost_exact'], '0.0120')           # 3 min x $0.004
        bad = self.api.patch(URL + '/rate', data=json.dumps({'billing_increment': 7}), content_type='application/json')
        self.assertEqual(bad.status_code, 400)

    def test_company_profit_for_a_day(self):
        # One day of October: the $350 monthly fee counts as 350 x 1/31.
        self.call(datetime(2026, 10, 27, 9, 0, tzinfo=ET), duration=6000)    # 100 min x 0.004 = 0.40
        self.call(datetime(2026, 10, 26, 9, 0, tzinfo=ET), duration=6000)    # another day
        r = self.api.get('/api/owner/company-profit', {'start': '2026-10-27'})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body['start'], body['end'], body['fee_share']), ('2026-10-27', '2026-10-27', '0.0323'))
        self.assertEqual(body['summary']['routing_minutes'], '0.40')
        self.assertEqual(body['summary']['routing_monthly_fee'], '11.29')    # 350 / 31
        self.assertEqual(body['profit']['carrier_costs'], '11.69')
        self.assertEqual(body['profit']['profit'], '-11.69')
        whole = self.api.get('/api/owner/company-profit', {'start': '2026-10-01', 'end': '2026-10-31'}).json()
        self.assertEqual(whole['fee_share'], '1.0000')
        self.assertEqual(whole['summary']['routing_monthly_fee'], '350.00')

    def test_company_profit_defaults_to_today(self):
        with mock.patch('django.utils.timezone.now', return_value=datetime(2026, 10, 27, 15, 0, tzinfo=ET)):
            body = self.api.get('/api/owner/company-profit').json()
        self.assertEqual((body['start'], body['end']), ('2026-10-27', '2026-10-27'))
