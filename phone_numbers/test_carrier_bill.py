"""CH-254: Carrier Costs is the whole bill, counted the way carriers bill.

Sir, 24 Oct: XOLO High Risk $15 a TFN, $0.40 a minute, $350 a month for its
account; XOLO forwarding $5 a TFN, $0.15 a minute; Freedom $5 a TFN; XOLO
termination $0.004 a minute and $350 a month.
"""
from datetime import datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from phone_numbers.models import Carrier, PhoneNumber, RoutingRate
from routing.models import CallLog

ET = ZoneInfo('America/New_York')


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class CarrierBillTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        owner = User.objects.create(username='o', email='o@x.io', organization=self.org,
                                    role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(owner)}')
        self.campaign = Campaign.objects.create(organization=self.org, name='C')
        self.xhr = Carrier.objects.create(organization=self.org, name='XOLO High Risk', code='XHR',
                                          number_monthly_cost=Decimal('15'), portal_monthly_fee=Decimal('350'),
                                          per_minute_cost=Decimal('0.40'))
        self.xlr = Carrier.objects.create(organization=self.org, name='XLR', code='XLR',
                                          per_minute_cost=Decimal('0.15'))
        PhoneNumber.objects.create(organization=self.org, number='+18770000001', carrier=self.xhr, vendor='Other')
        PhoneNumber.objects.create(organization=self.org, number='+18770000002', carrier=self.xlr, vendor='Other')
        RoutingRate.objects.update_or_create(key='termination', defaults={
            'per_minute_cost': Decimal('0.004'), 'monthly_fee': Decimal('350')})
        self.n = 0

    def call(self, number, seconds, at=None, forwarded=True):
        self.n += 1
        c = CallLog.objects.create(
            organization=self.org, campaign=self.campaign, caller_number='+1555000%04d' % self.n,
            called_number=number.lstrip('+'), twilio_call_sid='s%s' % self.n,
            status='completed', duration=seconds,
            destination_number='+18669990000' if forwarded else '')
        if at:
            CallLog.objects.filter(pk=c.pk).update(created_at=at)

    def get(self, month=''):
        r = self.api.get('/api/owner/carrier-costs' + (f'?month={month}' if month else ''))
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def by_code(self, body):
        return {c['code']: c for c in body['carriers']}

    def test_the_total_includes_routing(self):
        self.call('+18770000001', 600)   # 10 min on XOLO High Risk, forwarded
        b = self.get()
        s = b['summary']
        self.assertEqual(s['carriers_total'], '369.00')      # 15 + 350 + 10 x 0.40
        self.assertEqual(s['routing_minutes'], '0.04')       # 10 x 0.004
        self.assertEqual(s['routing_monthly_fee'], '350.00')
        self.assertEqual(s['routing_total'], '350.04')
        self.assertEqual(b['grand_total'], '719.04')
        self.assertEqual(s['grand_total'], '719.04')
        self.assertEqual(b['routing']['forwarded'], 1)

    def test_each_call_is_rounded_the_way_the_carrier_bills(self):
        self.call('+18770000001', 61)
        self.call('+18770000001', 1)
        g = self.by_code(self.get())['XHR']['totals']
        self.assertEqual((g['tcl_seconds'], g['billed_seconds']), (62, 62))
        r = self.api.patch(f'/api/owner/carriers/{self.xhr.id}/costs', {'billing_increment': 60},
                           content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        g = self.by_code(self.get())['XHR']
        self.assertEqual(g['billing_increment'], 60)
        self.assertEqual((g['totals']['tcl_seconds'], g['totals']['billed_seconds']), (62, 180))
        self.assertEqual(g['totals']['minute_charges'], '1.20')   # 3 billed minutes x 0.40
        Carrier.objects.filter(pk=self.xhr.pk).update(billing_increment=6)
        self.assertEqual(self.by_code(self.get())['XHR']['totals']['billed_seconds'], 72)  # 66 + 6

    def test_only_known_increments(self):
        r = self.api.patch(f'/api/owner/carriers/{self.xhr.id}/costs', {'billing_increment': 7},
                           content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_the_month_is_the_eastern_month(self):
        # 9 PM Eastern on 30 Sep is 1 Oct in UTC: it belongs to September.
        PhoneNumber.objects.update(created_at=datetime(2026, 8, 1, tzinfo=ET))
        self.call('+18770000001', 600, at=datetime(2026, 9, 30, 21, 0, tzinfo=ET))
        self.call('+18770000001', 60, at=datetime(2026, 10, 1, 1, 0, tzinfo=ET))
        self.assertEqual(self.by_code(self.get('2026-09'))['XHR']['totals']['tcl_seconds'], 600)
        oct_ = self.get('2026-10')
        self.assertEqual(self.by_code(oct_)['XHR']['totals']['tcl_seconds'], 60)
        self.assertEqual(oct_['routing']['seconds'], 60)

    def test_missing_rates_are_flagged(self):
        self.call('+18770000002', 120)
        PhoneNumber.objects.create(organization=self.org, number='+18770000003', vendor='Other')
        Carrier.objects.filter(pk=self.xlr.pk).update(per_minute_cost=0)
        kinds = {(w['kind'], w.get('code')) for w in self.get()['warnings']}
        self.assertIn(('no_minute_rate', 'XLR'), kinds)
        self.assertIn(('no_number_fee', 'XLR'), kinds)
        self.assertIn(('no_carrier', None), kinds)
        self.assertNotIn(('no_minute_rate', 'XHR'), kinds)
