"""CH-189: a TFN added under a carrier code gets the carrier's price by itself.

Run:  python manage.py test phone_numbers.test_tfn_price
"""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from phone_numbers.models import Carrier, PhoneNumber


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class TfnPriceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(username='owner', email='owner@x.io', organization=self.org,
                                         role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.kmq = Carrier.objects.create(organization=self.org, name='Freedom', code='KMQ',
                                          number_monthly_cost=Decimal('5'))
        self.xlr = Carrier.objects.create(organization=self.org, name='XLR', code='XLR')
        self.n = 0

    def number(self, carrier=None):
        self.n += 1
        return PhoneNumber.objects.create(organization=self.org, number=f'+1888891{self.n:04d}',
                                          carrier=carrier, vendor='Other')

    def price(self, n):
        n.refresh_from_db()
        return n.tfn_price

    def set_price(self, carrier, value):
        r = self.api.patch(f'/api/owner/carriers/{carrier.id}/costs', {'number_monthly_cost': value},
                           content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)

    def test_a_new_kmq_number_gets_5_by_itself(self):
        self.assertEqual(self.price(self.number(self.kmq)), Decimal('5.00'))

    def test_the_import_command_prices_every_number(self):
        out = StringIO()
        call_command('import_tfns', '--org', 'Avortyx', '--carrier', 'KMQ',
                     '--numbers', '18888844151,18888844205', stdout=out)
        self.assertIn('TFN price: $5', out.getvalue())
        prices = set(PhoneNumber.objects.filter(carrier=self.kmq).values_list('tfn_price', flat=True))
        self.assertEqual(prices, {Decimal('5.00')})

    def test_a_carrier_without_a_price_warns_and_prices_later(self):
        out = StringIO()
        call_command('import_tfns', '--org', 'Avortyx', '--carrier', 'XLR',
                     '--numbers', '18888910190', stdout=out)
        self.assertIn('no TFN price set', out.getvalue())
        n = PhoneNumber.objects.get(number='+18888910190')
        self.assertIsNone(n.tfn_price)
        self.set_price(self.xlr, '3')
        self.assertEqual(self.price(n), Decimal('3.00'))

    def test_a_price_change_applies_to_new_numbers_only(self):
        old = self.number(self.kmq)
        self.set_price(self.kmq, '6')
        new = self.number(self.kmq)
        self.assertEqual((self.price(old), self.price(new)), (Decimal('5.00'), Decimal('6.00')))

    def test_carrier_costs_bills_each_number_at_its_own_price(self):
        self.number(self.kmq)
        self.set_price(self.kmq, '6')
        self.number(self.kmq)
        body = self.api.get('/api/owner/carrier-costs').json()
        kmq = next(c for c in body['carriers'] if c['code'] == 'KMQ')
        self.assertEqual(sorted(r['number_fee'] for r in kmq['numbers']), ['5.00', '6.00'])
        self.assertEqual(kmq['totals']['number_fees'], '11.00')

    def test_assigning_a_carrier_prices_the_number(self):
        loose = self.number()
        self.assertIsNone(self.price(loose))
        r = self.api.post('/api/owner/carrier-costs/assign',
                          {'carrier_id': str(self.kmq.id), 'numbers': [str(loose.id)]},
                          content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.price(loose), Decimal('5.00'))

    def test_a_saved_number_keeps_its_price(self):
        n = self.number(self.kmq)
        Carrier.objects.filter(pk=self.kmq.pk).update(number_monthly_cost=Decimal('9'))
        n.label = 'renamed'
        n.save(update_fields=['label'])
        self.assertEqual(self.price(n), Decimal('5.00'))
