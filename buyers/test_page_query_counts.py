"""CH-521: the Destinations and Buyers pages no longer cost a query per row.

Measured on Server B, 4 Nov: /api/destinations/ took 212 ms and 197 queries for
189 lines (189 of them re-reading the workspace of each line); /api/buyers/ took
175 ms and 104 queries for 49 buyers (two COUNTs per buyer returning numbers
already counted). The answers are unchanged; only the repeated reads are gone.

Run:  python manage.py test buyers.test_page_query_counts
"""
from datetime import timedelta

from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.caps import buyer_usage, windows
from buyers.destination import Destination
from buyers.models import Buyer, BuyerCap
from routing.models import CallLog


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PageQueryCountTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.user = User.objects.create(username='m', email='m@x.io', organization=self.org,
                                        role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}')
        self.n = 0

    def add_buyers(self, count, tz=''):
        made = []
        for _ in range(count):
            self.n += 1
            b = Buyer.objects.create(organization=self.org, name=f'B{self.n}', timezone=tz)
            BuyerCap.objects.create(buyer=b, max_calls_daily=50, max_calls_monthly=500)
            Destination.objects.create(organization=self.org, buyer=b, name=f'B{self.n}|{self.n:04d}|',
                                       tfn=f'+1855470{self.n:04d}', enabled=True, daily_cap=10)
            CallLog.objects.create(organization=self.org, buyer=b, caller_number=f'+1555{self.n:07d}',
                                   called_number='+18888910343', twilio_call_sid=f's{self.n}',
                                   destination_number=f'+1855470{self.n:04d}', status='completed')
            made.append(b)
        return made

    def queries(self, path):
        with CaptureQueriesContext(connection) as q:
            r = self.api.get(path)
        self.assertEqual(r.status_code, 200, r.content)
        return len(q), r.json()

    def test_destinations_page_does_not_grow_with_lines(self):
        self.add_buyers(3)
        few, _ = self.queries('/api/destinations/?page=1&page_size=200')
        self.add_buyers(12)
        many, body = self.queries('/api/destinations/?page=1&page_size=200')
        self.assertEqual(few, many)
        row = next(d for d in body['items'] if d['name'] == 'B1|0001|')
        self.assertEqual((row['daily_calls'], row['global_calls'], row['buyer_name']), (1, 1, 'B1'))

    def test_buyers_page_does_not_grow_with_buyers(self):
        self.add_buyers(3)
        few, _ = self.queries('/api/buyers/')
        self.add_buyers(12)
        many, body = self.queries('/api/buyers/')
        self.assertEqual(few, many)
        row = next(b for b in body['items'] if b['name'] == 'B1')
        self.assertEqual((row['calls_today'], row['cap_used_today'], row['cap_used_month'], row['daily_cap']),
                         (1, 1, 1, 50))

    def test_a_buyer_on_its_own_day_is_still_counted_on_it(self):
        (la,) = self.add_buyers(1, tz='America/Los_Angeles')
        (et,) = self.add_buyers(1)
        now = timezone.now()
        # A call just before the Los Angeles day began: on LA's day it is
        # yesterday, whatever the Eastern day says.
        _, la_day, _ = windows('America/Los_Angeles', now)
        for b in (la, et):
            c = CallLog.objects.create(organization=self.org, buyer=b, caller_number='+15550009999',
                                       called_number='+18888910343', twilio_call_sid=f'early-{b.name}',
                                       destination_number='+18554709999', status='completed')
            CallLog.objects.filter(pk=c.pk).update(created_at=la_day - timedelta(minutes=1))
        usage = buyer_usage(self.org, [la.id, et.id], now=now)
        _, et_day, _ = windows('America/New_York', now)
        sent = CallLog.objects.filter(retry_hidden=False).exclude(destination_number='')
        self.assertEqual(usage[la.id]['cap_used_today'], sent.filter(buyer=la, created_at__gte=la_day).count())
        self.assertEqual(usage[et.id]['cap_used_today'], sent.filter(buyer=et, created_at__gte=et_day).count())
        self.assertEqual(usage[et.id]['cap_used_today'], usage[et.id]['calls_today'])
