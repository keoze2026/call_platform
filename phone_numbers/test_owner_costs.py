"""Owner > Carrier Costs (CH-154)."""
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from phone_numbers.models import Carrier, PhoneNumber
from phone_numbers.services import PhoneNumberService
from routing.models import CallLog


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class CarrierCostsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(
            username='owner', email='owner@x.io', organization=self.org,
            role='admin', is_superuser=True)
        self.admin = User.objects.create(
            username='adm', email='adm@x.io', organization=self.org, role='admin')
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.campaign = Campaign.objects.create(organization=self.org, name='C')
        self.xolo = Carrier.objects.create(
            organization=self.org, name='Xolo High Risk', code='HYU',
            number_monthly_cost=Decimal('15'), portal_monthly_fee=Decimal('350'),
            per_minute_cost=Decimal('0.40'))
        self.freedom = Carrier.objects.create(
            organization=self.org, name='Freedom', code='KMQ',
            number_monthly_cost=Decimal('6'), per_minute_cost=Decimal('0.04'))
        self.a = PhoneNumber.objects.create(
            organization=self.org, number='+18774893791', carrier=self.xolo, vendor='Other')
        self.b = PhoneNumber.objects.create(
            organization=self.org, number='+18774893792', carrier=self.freedom, vendor='Other')
        self.n = 0

    def call(self, number, seconds, status='completed'):
        self.n += 1
        CallLog.objects.create(
            organization=self.org, campaign=self.campaign, caller_number='+1555000%04d' % self.n,
            called_number=number.lstrip('+'), twilio_call_sid='s%s' % self.n,
            status=status, duration=seconds)

    def get(self, q=''):
        return self.client.get('/api/owner/carrier-costs' + q)

    def by_code(self, body):
        return {c['code']: c for c in body['carriers']}

    def test_each_carrier_gets_its_own_group_with_fees_and_minutes(self):
        self.call('+18774893791', 120)   # 2 min
        self.call('+18774893791', 60)    # 1 min
        self.call('+18774893791', 99, status='no_answer')
        r = self.get()
        self.assertEqual(r.status_code, 200, r.content)
        g = self.by_code(r.json())
        t = g['HYU']['totals']
        self.assertEqual(t['calls'], 3)
        self.assertEqual(t['connected'], 2)
        self.assertEqual(t['tcl_seconds'], 180)
        self.assertEqual(t['aht_seconds'], 90)
        self.assertEqual(t['number_fees'], '15.00')
        self.assertEqual(t['portal_fee'], '350.00')
        self.assertEqual(t['minute_charges'], '1.20')
        self.assertEqual(t['monthly_bill'], '366.20')
        self.assertEqual(g['KMQ']['totals']['monthly_bill'], '6.00')
        self.assertEqual(r.json()['grand_total'], '372.20')

    def test_a_deleted_number_leaves_the_user_list_but_stays_for_the_owner(self):
        self.call('+18774893791', 60)
        PhoneNumberService.release_number(str(self.a.id), self.admin)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'released')
        self.assertIsNotNone(self.a.released_at)
        listed = [n.number for n in PhoneNumberService.list_numbers(self.admin)]
        self.assertNotIn('+18774893791', listed)
        row = self.by_code(self.get().json())['HYU']['numbers'][0]
        self.assertTrue(row['deleted'])
        self.assertEqual(row['number'], '+18774893791')
        self.assertEqual(row['number_fee'], '15.00')
        self.assertEqual(row['connected'], 1)

    def test_a_number_deleted_before_the_month_is_not_billed_in_it(self):
        PhoneNumber.objects.filter(pk=self.a.pk).update(
            status='released', released_at=timezone.now() - timedelta(days=90))
        g = self.by_code(self.get().json())
        self.assertEqual(g['HYU']['totals']['numbers'], 0)
        self.assertEqual(g['HYU']['totals']['monthly_bill'], '350.00')

    def test_numbers_without_a_carrier_are_listed_apart(self):
        PhoneNumber.objects.create(organization=self.org, number='+18774893793', vendor='Other')
        u = self.get().json()['unassigned']
        self.assertEqual([n['number'] for n in u['numbers']], ['+18774893793'])

    def test_bad_month_is_a_400(self):
        self.assertEqual(self.get('?month=2026-13').status_code, 400)
        self.assertEqual(self.get('?month=abc').status_code, 400)

    def test_only_the_owner_can_see_or_change_costs(self):
        c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')
        self.assertEqual(c.get('/api/owner/carrier-costs').status_code, 403)
        r = c.patch(f'/api/owner/carriers/{self.xolo.id}/costs', {'per_minute_cost': '9'},
                    content_type='application/json')
        self.assertEqual(r.status_code, 403)
        self.xolo.refresh_from_db()
        self.assertEqual(self.xolo.per_minute_cost, Decimal('0.4000'))

    def test_owner_sets_the_rate_card(self):
        r = self.client.patch(
            f'/api/owner/carriers/{self.freedom.id}/costs',
            {'number_monthly_cost': '6', 'per_minute_cost': '0.04', 'portal_monthly_fee': '0'},
            content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        bad = self.client.patch(
            f'/api/owner/carriers/{self.freedom.id}/costs', {'per_minute_cost': '-1'},
            content_type='application/json')
        self.assertEqual(bad.status_code, 400)
        junk = self.client.patch(
            f'/api/owner/carriers/{self.freedom.id}/costs', {'number_monthly_cost': 'abc'},
            content_type='application/json')
        self.assertEqual(junk.status_code, 400)

    def test_assign_tags_numbers_only_inside_the_carriers_workspace(self):
        other = Organization.objects.create(name='Other', slug='other')
        foreign = PhoneNumber.objects.create(organization=other, number='+18005550000', vendor='Other')
        loose = PhoneNumber.objects.create(organization=self.org, number='+18774893794', vendor='Other')
        r = self.client.post('/api/owner/carrier-costs/assign',
                             {'carrier_id': str(self.xolo.id), 'numbers': [str(loose.id), str(foreign.id)]},
                             content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {'updated': 1, 'skipped': 1})
        loose.refresh_from_db(); foreign.refresh_from_db()
        self.assertEqual(loose.carrier_id, self.xolo.id)
        self.assertIsNone(foreign.carrier_id)
