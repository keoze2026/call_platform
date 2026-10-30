"""CH-423: the Publishers list carries each publisher's calls, counted as the
Reports page counts them, and the page's totals.

Run:  python manage.py test publishers.test_publisher_list_counts
"""
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from billing.models import BillingAccount, Transaction
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PublisherListCountsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='a', email='a@x.io', organization=self.org,
                                         role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')
        self.pub = Publisher.objects.create(organization=self.org, name='ADS-1')
        self.quiet = Publisher.objects.create(organization=self.org, name='Free')
        self.c13 = Campaign.objects.create(organization=self.org, name='C-13', payout_amount=Decimal('1.00'))
        for n in ('+18005550101', '+18005550102', '+18005550103'):
            PhoneNumber.objects.create(organization=self.org, number=n, publisher=self.pub, campaign=self.c13)

    def call(self, **kw):
        base = dict(organization=self.org, publisher_id=self.pub.id, campaign=self.c13, campaign_name='C-13',
                    caller_number='+15550000001', called_number='+18005550101', started_at=timezone.now())
        base.update(kw)
        return CallRecord.objects.create(**base)

    def row(self, body, name):
        return next(x for x in body['items'] if x['name'] == name)

    def test_payable_answered_and_sent_today(self):
        self.call(status='completed', is_converted=True)                    # paid
        self.call(status='completed', is_converted=True)                    # paid
        self.call(status='completed', is_converted=False)                   # answered, not paid
        self.call(status='no_answer')                                       # sent only
        self.call(status='completed', is_converted=True,                    # paid, last month-ish
                  started_at=timezone.now() - timedelta(days=40))
        r = self.api.get('/api/publishers/')
        self.assertEqual(r.status_code, 200, r.content)
        row = self.row(r.json(), 'ADS-1')
        self.assertEqual((row['payable_today'], row['answered_today'], row['sent_today']), (2, 3, 4))
        self.assertEqual(row['calls_hour'], 2)
        self.assertEqual(row['payable_global'], 3)
        self.assertEqual(row['tfn_count'], 3)
        self.assertEqual([c['name'] for c in row['campaigns']], ['C-13'])
        quiet = self.row(r.json(), 'Free')
        self.assertEqual((quiet['payable_today'], quiet['sent_today'], quiet['tfn_count']), (0, 0, 0))
        self.assertEqual(quiet['campaigns'], [])

    def test_summary_payable_by_hour_and_pending_payouts(self):
        self.call(status='completed', is_converted=True)
        self.call(status='completed', is_converted=True)
        acct = BillingAccount.objects.create(organization=self.org, balance=Decimal('100'))
        Transaction.objects.create(organization=self.org, billing_account=acct, transaction_type='payout', status='pending',
                                   amount=Decimal('12.50'), publisher_name='ADS-1')
        s = self.api.get('/api/publishers/').json()['summary']
        self.assertEqual(s['payable_today'], 2)
        self.assertEqual(sum(s['payable_by_hour']), 2)
        self.assertEqual(len(s['payable_by_hour']), 24)
        self.assertEqual((s['pending_payouts'], s['pending_payout_amount']), (1, '12.50'))
