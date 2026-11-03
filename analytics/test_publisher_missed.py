"""CH-481: a missed call the buyer converts on "Incoming Call" is Missed for the
publisher when the tracking number pays on Call Connected.

Run:  python manage.py test analytics.test_publisher_missed
"""
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher

TFN = '+18005550100'


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PublisherMissedTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='a', email='a@x.io', organization=self.org,
                                         role='admin', is_active=True)
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO 2')
        self.pub_login = User.objects.create(username='p', email='p@x.io', organization=self.org,
                                             role='publisher', publisher=self.pub, is_active=True)
        self.campaign = Campaign.objects.create(organization=self.org, name='Medicare')
        self.number = PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='other',
                                                 publisher=self.pub, payout_on='connected')
        now = timezone.now()
        common = dict(organization=self.org, called_number=TFN, campaign=self.campaign,
                      publisher_id=self.pub.id, publisher_name=self.pub.name, started_at=now)
        # Missed, but its buyer pays on Incoming Call: converted for us.
        self.missed = CallRecord.objects.create(
            caller_number='+15550000001', status='no_answer', is_converted=True,
            billing_rule='incoming', revenue=Decimal('10'), payout=Decimal('5'), **common)
        # Answered and converted: converted for everyone.
        self.answered = CallRecord.objects.create(
            caller_number='+15550000002', status='completed', duration_seconds=120, is_converted=True,
            billing_rule='incoming', revenue=Decimal('10'), payout=Decimal('5'), **common)

    def log(self, user):
        today = timezone.now().date().isoformat()
        r = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}').get(
            f'/api/analytics/calls?date_from={today}&date_to={today}&timezone=UTC')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        items = body.get('items') or []
        self.assertEqual(len(items), 2)
        return {('answered' if i['status'] == 'completed' else 'missed'): i for i in items}

    def dashboard(self, user):
        today = timezone.now().date().isoformat()
        r = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}').get(
            f'/api/analytics/dashboard?date_from={today}&date_to={today}&timezone=UTC')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_we_still_see_missed_with_conversion(self):
        rows = self.log(self.admin)
        self.assertTrue(rows['missed']['is_converted'])
        self.assertEqual(self.dashboard(self.admin)['converted_calls'], 2)

    def test_publisher_sees_missed_on_call_connected(self):
        rows = self.log(self.pub_login)
        self.assertFalse(rows['missed']['is_converted'])
        self.assertTrue(rows['answered']['is_converted'])
        d = self.dashboard(self.pub_login)
        self.assertEqual(d['converted_calls'], 1)
        self.assertEqual(Decimal(str(d['total_payout'])), Decimal('5'))

    def test_call_length_rule_also_needs_an_answer(self):
        self.number.payout_on = 'length'
        self.number.save()
        self.assertFalse(self.log(self.pub_login)['missed']['is_converted'])

    def test_payout_on_converted_follows_the_buyer(self):
        self.number.payout_on = 'converted'
        self.number.save()
        rows = self.log(self.pub_login)
        self.assertTrue(rows['missed']['is_converted'])
        self.assertEqual(self.dashboard(self.pub_login)['converted_calls'], 2)

    def test_publisher_export_says_missed(self):
        self.pub.permissions = {'download_reports': True}
        self.pub.save()
        today = timezone.now().date().isoformat()
        r = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.pub_login)}').get(
            f'/api/analytics/calls/export?date_from={today}&date_to={today}&timezone=UTC')
        self.assertEqual(r.status_code, 200)
        body = b''.join(r.streaming_content).decode() if r.streaming else r.content.decode()
        self.assertNotIn('Missed With Conversion', body)
