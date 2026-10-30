"""Owner > Carrier Costs statements (CH-433)."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
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
class CarrierStatementTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(
            username='owner', email='owner@x.io', organization=self.org, role='admin', is_superuser=True)
        self.admin = User.objects.create(username='adm', email='adm@x.io', organization=self.org, role='admin')
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.campaign = Campaign.objects.create(organization=self.org, name='C')
        self.xlr = Carrier.objects.create(
            organization=self.org, name='XOLO', code='XLR', risk_override='low',
            number_monthly_cost=Decimal('5'), per_minute_cost=Decimal('0.0150'))
        self.xhr = Carrier.objects.create(
            organization=self.org, name='Xolo High Risk', code='XHR', risk_override='high',
            number_monthly_cost=Decimal('15'), portal_monthly_fee=Decimal('350'),
            per_minute_cost=Decimal('0.0400'))
        self.kmq = Carrier.objects.create(
            organization=self.org, name='Freedom', code='KMQ',
            number_monthly_cost=Decimal('5'), per_minute_cost=Decimal('0.0035'), billing_increment=6)
        RoutingRate.objects.create(
            key='termination', name='XOLO TFN termination',
            per_minute_cost=Decimal('0.0040'), monthly_fee=Decimal('350'))
        self.seq = 0
        # 10 XLR numbers added 2 Oct, 1 XHR, 2 KMQ (one deleted 14 Oct, one in September).
        self.xlr_numbers = [self.number(f'+1888891{3400 + i}', self.xlr, et(2)) for i in range(10)]
        self.xhr_number = self.number('+18552026099', self.xhr, et(1))
        self.kmq_live = self.number('+18448158851', self.kmq, et(1))
        self.kmq_gone = self.number('+18776302534', self.kmq, et(1), released=et(14))
        self.kmq_old = self.number('+18776302818', self.kmq, datetime(2026, 8, 1, tzinfo=ET),
                                   released=datetime(2026, 9, 20, tzinfo=ET))

    def number(self, num, carrier, added, released=None):
        n = PhoneNumber.objects.create(organization=self.org, number=num, carrier=carrier, vendor='Other')
        PhoneNumber.objects.filter(pk=n.pk).update(
            created_at=added, released_at=released, status='released' if released else 'active')
        n.refresh_from_db()
        return n

    def call(self, number, seconds, when, status='completed', dest='', hidden=False):
        self.seq += 1
        c = CallLog.objects.create(
            organization=self.org, campaign=self.campaign, caller_number='+1555000%04d' % self.seq,
            called_number=number.lstrip('+'), twilio_call_sid='s%s' % self.seq,
            status=status, duration=seconds, destination_number=dest, retry_hidden=hidden)
        CallLog.objects.filter(pk=c.pk).update(created_at=when)
        return c

    def get(self, start, end=None):
        q = f'?start={start}' + (f'&end={end}' if end else '')
        r = self.client.get('/api/owner/carrier-statement' + q)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def company(self, body, name):
        return next(c for c in body['carriers'] if c['name'] == name)

    def code(self, company, code):
        return next(c for c in company['codes'] if c['code'] == code)

    def test_one_day_groups_codes_by_carrier_and_prorates_the_month(self):
        self.call(self.xlr_numbers[0].number, 600, et(30))           # 10 min
        self.call(self.xlr_numbers[1].number, 90, et(30))            # 1.5 min
        self.call(self.xlr_numbers[2].number, 0, et(30), status='no_answer')
        self.call('+15550001111', 300, et(30), dest='+18005550000')  # forwarded, 5 min
        body = self.get('2026-10-30')
        self.assertEqual(body['month_days'], 31)
        self.assertFalse(body['whole_month'])
        xolo = self.company(body, 'XOLO')
        self.assertEqual([c['code'] for c in xolo['codes']], ['XLR', 'XHR'])  # low risk first
        xlr = self.code(xolo, 'XLR')
        self.assertEqual(xlr['tfns'], 10)
        self.assertEqual(xlr['tfn_rate'], '5.00')
        self.assertEqual(xlr['tfn_fees'], '1.61')              # 10 x $5 / 31
        self.assertEqual(xlr['calls'], 3)
        self.assertEqual(xlr['connected'], 2)
        self.assertEqual(xlr['tcl_seconds'], 690)
        self.assertEqual(xlr['minutes'], '11.50')
        self.assertEqual(xlr['acl_seconds'], 345)
        self.assertEqual(xlr['minute_cost'], '0.17')           # 11.50 x $0.015 = 0.1725
        self.assertEqual(self.code(xolo, 'XHR')['tfn_fees'], '0.48')
        term = xolo['termination']
        self.assertEqual(term['calls'], 1)
        self.assertEqual(term['minutes'], '5.00')
        self.assertEqual(term['minute_cost'], '0.02')
        monthly = {m['code']: m['amount'] for m in xolo['monthly']}
        self.assertEqual(monthly, {'XHR': '11.29', 'termination': '11.29'})
        t = xolo['totals']
        self.assertEqual(t['tfn_fees'], '2.09')
        self.assertEqual(t['monthly'], '22.58')
        self.assertEqual(Decimal(t['total']),
                         Decimal(t['tfn_fees']) + Decimal(t['minute_cost']) + Decimal(t['monthly']))

    def test_a_tfn_deleted_this_month_is_still_billed_one_deleted_before_it_is_not(self):
        body = self.get('2026-10-30')
        freedom = self.company(body, 'Freedom')
        kmq = self.code(freedom, 'KMQ')
        self.assertEqual((kmq['tfns'], kmq['live'], kmq['deleted']), (2, 1, 1))
        self.assertEqual(kmq['tfn_fees'], '0.32')              # 2 x $5 / 31
        listed = {n['number']: n for n in freedom['numbers']}
        self.assertNotIn('+18776302818', listed)
        self.assertTrue(listed['+18776302534']['deleted'])
        self.assertEqual(listed['+18776302534']['billed'], '0.16')

    def test_the_whole_month_bills_whole_monthly_fees(self):
        body = self.get('2026-10-01', '2026-10-31')
        self.assertTrue(body['whole_month'])
        xolo = self.company(body, 'XOLO')
        self.assertEqual(self.code(xolo, 'XLR')['tfn_fees'], '50.00')
        self.assertEqual({m['code']: m['amount'] for m in xolo['monthly']}, {'XHR': '350.00', 'termination': '350.00'})
        self.assertEqual(self.code(self.company(body, 'Freedom'), 'KMQ')['tfn_fees'], '10.00')

    def test_a_tfn_added_after_the_period_is_not_billed_in_it(self):
        body = self.get('2026-10-01')   # the XLR numbers came on 2 Oct
        codes = [c['code'] for c in self.company(body, 'XOLO')['codes']]
        self.assertNotIn('XLR', codes)

    def test_rounding_up_to_six_seconds_for_freedom(self):
        self.call(self.kmq_live.number, 61, et(30))   # billed 66 s
        kmq = self.code(self.company(self.get('2026-10-30'), 'Freedom'), 'KMQ')
        self.assertEqual(kmq['tcl_seconds'], 61)
        self.assertEqual(kmq['billed_seconds'], 66)
        self.assertEqual(kmq['minutes'], '1.10')

    def test_hidden_retries_are_not_counted_twice_but_their_seconds_are_billed(self):
        self.call(self.xlr_numbers[0].number, 30, et(30), hidden=True)
        self.call(self.xlr_numbers[0].number, 60, et(30))
        xlr = self.code(self.company(self.get('2026-10-30'), 'XOLO'), 'XLR')
        self.assertEqual(xlr['calls'], 1)
        self.assertEqual(xlr['tcl_seconds'], 90)

    def test_totals_and_tfn_counts(self):
        body = self.get('2026-10-30')
        self.assertEqual(body['tfns'], {'total': 13, 'live': 12, 'deleted': 1})
        self.assertEqual(Decimal(body['total']), sum(Decimal(c['totals']['total']) for c in body['carriers']))

    def test_cdrs_list_each_leg_with_its_cost(self):
        self.call(self.kmq_live.number, 61, et(30), dest='+18005550000')
        rows = self.client.get('/api/owner/carrier-statement/cdrs?start=2026-10-30').json()['rows']
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r['code'], r['carrier'], r['billed_seconds'], r['termination_seconds']), ('KMQ', 'Freedom', 66, 61))
        self.assertEqual(r['cost'], '0.0039')
        self.assertEqual(r['termination_cost'], '0.0041')

    def test_owner_only_and_bad_dates(self):
        other = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')
        self.assertEqual(other.get('/api/owner/carrier-statement').status_code, 403)
        self.assertEqual(self.client.get('/api/owner/carrier-statement?start=2026-10-31&end=2026-10-01').status_code, 400)
