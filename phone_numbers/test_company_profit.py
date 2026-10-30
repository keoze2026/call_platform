"""Owner > Company Profit page (CH-434)."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from billing.models import BillingAccount, Transaction
from campaigns.models import Campaign
from phone_numbers.models import Carrier, PhoneNumber, RoutingRate
from routing.models import CallLog

ET = ZoneInfo('America/New_York')


def et(day, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=ET)


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class CompanyProfitTests(TestCase):
    def setUp(self):
        self.ours = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(
            username='owner', email='owner@x.io', organization=self.ours, role='admin', is_superuser=True)
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.cust = Organization.objects.create(name='Acme Calls', slug='acme')
        self.acc = BillingAccount.objects.create(organization=self.cust)
        self.camp = Campaign.objects.create(organization=self.cust, name='C')
        self.xlr = Carrier.objects.create(
            organization=self.ours, name='XOLO', code='XLR', risk_override='low',
            number_monthly_cost=Decimal('5'), per_minute_cost=Decimal('0.0150'))
        self.hyu = Carrier.objects.create(
            organization=self.ours, name='XOLO', code='HYU', risk_override='high',
            number_monthly_cost=Decimal('15'), per_minute_cost=Decimal('0.0400'))
        RoutingRate.objects.create(key='termination', name='XOLO TFN termination',
                                   per_minute_cost=Decimal('0.0040'), monthly_fee=Decimal('0'))
        n = PhoneNumber.objects.create(organization=self.cust, number='+18888913475', carrier=self.xlr, vendor='Other')
        PhoneNumber.objects.filter(pk=n.pk).update(created_at=et(1))
        self.seq = 0

    def call(self, seconds, recorded=False, checked=False, org=None):
        self.seq += 1
        sid = 's%d' % self.seq
        c = CallLog.objects.create(
            organization=org or self.cust, campaign=self.camp, caller_number='+1555000%04d' % self.seq,
            called_number='18888913475', twilio_call_sid=sid, status='completed', duration=seconds,
            destination_number='+18005550000', recording_url='https://r/x.mp3' if recorded else '',
            ipqs_checked=checked)
        CallLog.objects.filter(pk=c.pk).update(created_at=et(30))
        return sid

    def charge(self, amount, description, sid='', org=None):
        org = org or self.cust
        t = Transaction.objects.create(
            organization=org, billing_account=BillingAccount.objects.get(organization=org),
            transaction_type='charge', amount=Decimal(amount), description=description,
            call_sid=sid, status='completed')
        Transaction.objects.filter(pk=t.pk).update(created_at=et(30))

    def get(self, q='?start=2026-10-30'):
        r = self.client.get('/api/owner/company-profit-statement' + q)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_a_call_charge_is_split_into_minutes_recording_and_voip_and_adds_up(self):
        # 90 s recorded and checked: 2 min x 0.045 + 2 x 0.0025 + 0.01 = 0.105
        sid = self.call(90, recorded=True, checked=True)
        self.charge('0.1050', 'Call charge', sid)
        self.charge('0.0250', 'Rejected call fee + VoIP Shield')
        self.charge('20.00', 'Tracking number +18888913476')
        self.charge('49.99', 'Portal access fee')
        body = self.get()
        lines = {x['key']: x for x in body['lines']}
        self.assertEqual((lines['minutes']['amount'], lines['minutes']['quantity']), ('0.09', 2))
        self.assertEqual((lines['recording']['amount'], lines['recording']['quantity']), ('0.01', 2))
        self.assertEqual((lines['voip']['amount'], lines['voip']['quantity']), ('0.02', 2))
        self.assertEqual((lines['rejected']['amount'], lines['rejected']['quantity']), ('0.01', 1))
        self.assertEqual(sum(Decimal(x['amount']) for x in body['lines']), Decimal('70.12'))
        self.assertEqual(lines['tfn']['amount'], '20.00')
        self.assertEqual((lines['portal']['amount'], lines['portal']['quantity']), ('49.99', 1))
        self.assertEqual(body['charged'], '70.12')
        self.assertEqual(body['customers'], 1)
        self.assertEqual(Decimal(body['profit']), Decimal(body['charged']) - Decimal(body['carrier_costs']))

    def test_our_own_workspace_is_not_a_customer(self):
        BillingAccount.objects.create(organization=self.ours)
        self.charge('49.99', 'Portal access fee', org=self.ours)
        body = self.get()
        self.assertEqual(body['charged'], '0.00')
        self.assertEqual(body['customers'], 0)
        self.assertIsNone(body['margin_pct'])

    def test_carrier_costs_are_the_carrier_statement_total(self):
        self.call(600)
        body = self.get()
        stmt = self.client.get('/api/owner/carrier-statement?start=2026-10-30').json()
        self.assertEqual(body['carrier_costs'], stmt['total'])
        self.assertEqual([c['total'] for c in body['carriers']], [c['totals']['total'] for c in stmt['carriers']])

    def test_what_if_bills_each_call_rounded_up_to_the_minute(self):
        self.call(61)    # 2 minutes billed
        self.call(120)   # 2 minutes
        w = self.get()['what_if']
        self.assertEqual(w['connected_minutes'], '3.02')
        self.assertEqual(w['billed_minutes'], 4)
        self.assertEqual(w['customer_minutes'], '0.18')            # 4 x 0.045
        self.assertEqual(w['customer_minutes_recording'], '0.19')  # 4 x 0.0475

    def test_by_customer_charges_against_its_own_traffic(self):
        sid = self.call(600)       # 10 min on its XLR TFN, forwarded
        self.charge('0.4500', 'Call charge', sid)
        row = self.get()['by_customer'][0]
        self.assertEqual(row['name'], 'Acme Calls')
        self.assertEqual(row['minutes'], 10)
        self.assertEqual(row['charged'], '0.45')
        # 10 min x 0.015 + 10 min x 0.004 termination + $5 / 31 TFN fee
        self.assertEqual(row['our_cost'], '0.35')
        self.assertEqual(row['profit'], '0.10')

    def test_price_vs_cost_per_minute(self):
        rows = {tuple(r['codes']): r for r in self.get()['price_vs_cost']}
        self.assertEqual(rows[('XLR',)]['our_cost'], '0.0190')
        self.assertEqual(rows[('XLR',)]['customer_price'], '0.0475')
        self.assertEqual(rows[('XLR',)]['margin'], '0.0285')
        self.assertEqual(rows[('XLR',)]['margin_pct'], '60.0')
        hyu = rows[('HYU',)]
        self.assertEqual((hyu['margin'], hyu['margin_pct']), ('0.0035', '7.4'))
        self.assertEqual((hyu['margin_no_recording'], hyu['margin_no_recording_pct']), ('0.0010', '2.2'))

    def test_owner_only(self):
        admin = User.objects.create(username='adm', email='adm@x.io', organization=self.ours, role='admin')
        c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        self.assertEqual(c.get('/api/owner/company-profit-statement').status_code, 403)
