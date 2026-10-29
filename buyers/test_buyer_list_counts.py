"""CH-405: the Buyers list counts what routing counts.

Sir, 30 Oct: "set correct metrics which works". The list cut the day at
midnight UTC (8 PM Eastern), counted refusals and hidden Revo retries as calls,
and showed the publisher's payout as the buyer's spend.

Run:  python manage.py test buyers.test_buyer_list_counts
"""
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer, BuyerCap
from routing.models import CallLog

ET = ZoneInfo('America/New_York')
TFN = '+18554707586'


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class BuyerListCountsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.user = User.objects.create(username='m', email='m@x.io', organization=self.org,
                                        role='admin', is_superuser=True)
        self.bing = Buyer.objects.create(organization=self.org, name='Bing')
        BuyerCap.objects.create(buyer=self.bing, max_calls_daily=150)
        for i, cap in enumerate((20, 30, 0)):
            Destination.objects.create(organization=self.org, buyer=self.bing, name=f'Bing {i}',
                                       tfn=f'+1855470758{i}', enabled=True, daily_cap=cap)
        Destination.objects.create(organization=self.org, buyer=self.bing, name='Bing off',
                                   tfn='+18554707599', enabled=False, daily_cap=99)
        self.n = 0

    def call(self, **extra):
        self.n += 1
        return CallLog.objects.create(organization=self.org, buyer=self.bing,
                                      caller_number=f'+1555000{self.n:04d}', called_number='+18888910343',
                                      twilio_call_sid=f's-{self.n}', destination_number=TFN,
                                      status='completed', **extra)

    def row(self):
        api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}')
        r = api.get('/api/buyers/')
        self.assertEqual(r.status_code, 200, r.content)
        return next(b for b in r.json()['items'] if b['name'] == 'Bing')

    def test_counts_spend_and_caps(self):
        self.call(revenue=Decimal('1.50'))
        self.call(revenue=Decimal('2.00'))
        self.call(block_reason='No Destination')          # refused: never sent
        self.call(retry_hidden=True)                        # a Revo retry already counted
        before = self.call(revenue=Decimal('9.00'))         # yesterday evening, Eastern
        midnight = timezone.localtime(timezone.now(), ET).replace(hour=0, minute=0, second=0, microsecond=0)
        CallLog.objects.filter(pk=before.pk).update(created_at=midnight - timedelta(hours=1))

        row = self.row()
        self.assertEqual(row['calls_today'], 2)
        self.assertEqual(Decimal(row['spend_today']), Decimal('3.50'))
        self.assertEqual(row['calls_global'], 3)
        self.assertEqual((row['daily_cap'], row['cap_used_today']), (150, 2))
        self.assertEqual((row['tfn_count'], row['tfn_cap_total'], row['tfn_uncapped']), (3, 50, 1))
