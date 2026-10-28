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

    def test_the_days_expenses_line_by_line(self):
        """CH-336: TFN cost, each carrier's minutes and cost, routing, total - for today."""
        import calendar
        from django.utils import timezone as tz
        self.call('+18770000001', 600)          # 10 min on XOLO High Risk, forwarded
        self.call('+18770000002', 120)          # 2 min on XLR, forwarded
        r = self.api.get('/api/owner/expenses')
        self.assertEqual(r.status_code, 200, r.content)
        e = r.json()
        today = tz.now().astimezone(ET).date()
        share = Decimal(1) / Decimal(calendar.monthrange(today.year, today.month)[1])
        cents = lambda d: str(Decimal(d).quantize(Decimal('0.01'), rounding='ROUND_HALF_UP'))
        self.assertEqual(e['start'], today.isoformat())
        by = {c['code']: c for c in e['carriers']}
        self.assertEqual((by['XHR']['minutes'], by['XHR']['cost']), ('10.00', '4.00'))      # 10 x 0.40
        self.assertEqual((by['XLR']['minutes'], by['XLR']['cost']), ('2.00', '0.30'))       # 2 x 0.15
        self.assertEqual(e['tfn']['numbers'], 2)
        self.assertEqual(e['tfn']['cost'], cents(Decimal(15) * share))                     # one $15 TFN, by the day
        self.assertEqual(e['monthly_charges'], cents(Decimal(350) * share))
        self.assertEqual((e['routing']['minutes'], e['routing']['cost']), ('12.00', '0.05'))  # 12 x 0.004
        self.assertEqual(e['routing']['fee'], cents(Decimal(350) * share))
        # The total is the profit card's carrier costs for the same day.
        p = self.api.get('/api/owner/company-profit').json()['profit']
        self.assertEqual(e['total'], p['carrier_costs'])

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


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class CompanyProfitTests(TestCase):
    """CH-257: company profit before tax = customer charges - carrier costs."""

    def setUp(self):
        from billing.models import BillingAccount
        self.ours = Organization.objects.create(name='Avortyx', slug='avx')
        owner = User.objects.create(username='o', email='o@x.io', organization=self.ours,
                                    role='admin', is_superuser=True, is_active=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(owner)}')
        self.cust = Organization.objects.create(name='texora', slug='texora')
        self.accounts = {o: BillingAccount.objects.create(organization=o, balance=Decimal('100'))
                         for o in (self.ours, self.cust)}
        Carrier.objects.create(organization=self.ours, name='XHR', code='XHR', portal_monthly_fee=Decimal('350'))
        RoutingRate.objects.update_or_create(key='termination', defaults={
            'per_minute_cost': Decimal('0'), 'monthly_fee': Decimal('350')})

    def tx(self, org, amount, description, kind='charge'):
        from billing.models import Transaction
        Transaction.objects.create(organization=org, billing_account=self.accounts[org], transaction_type=kind,
                                   amount=Decimal(amount), description=description, status='completed')

    def test_profit_is_customer_charges_minus_carrier_costs(self):
        self.tx(self.cust, '0.09', 'Call charge')
        self.tx(self.cust, '0.025', 'Rejected call fee + VoIP Shield')
        self.tx(self.cust, '0.01', 'VoIP Shield fee')
        self.tx(self.cust, '49.99', 'Portal access fee (Oct 2026)')
        self.tx(self.cust, '20', 'Tracking number +18885550000')
        self.tx(self.cust, '5', 'Refund', kind='refund')
        self.tx(self.ours, '123', 'Call charge')            # our own portal: not income
        p = self.api.get('/api/owner/carrier-costs').json()['profit']
        self.assertEqual((p['calls'], p['rejected'], p['voip'], p['portal'], p['tfn']),
                         ('0.09', '0.03', '0.01', '49.99', '20.00'))
        self.assertEqual(p['refunds'], '5.00')
        self.assertEqual(p['income'], '65.12')              # 70.115 - 5 = 65.115, half up
        self.assertEqual(p['customers'], 1)
        self.assertEqual(p['carrier_costs'], '700.00')      # XHR $350 + routing $350
        self.assertEqual(p['profit'], '-634.88')            # 65.12 - 700
