"""CH-622: three pages the tenant check (6 Nov) found failing with a server
error (500) - none of them gave out data, but each must answer properly.

  - caller profile: called a CallerProfileService that no longer exists
  - number-pool sessions, webhook deliveries: "not found" was not declared,
    so another account's (or a missing) record raised 500 instead of 404

Run:  python manage.py test analytics.test_ch622_pages
"""
import uuid

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.models import Buyer
from campaigns.models import Campaign
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(ALLOWED_HOSTS=['*'], CACHES=CACHE)
class Ch622PagesTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='a', email='a@x.io', organization=self.org,
                                         role='admin', is_active=True)
        self.bop = Buyer.objects.create(organization=self.org, name='BOP')
        self.buyer = User.objects.create(username='b', email='b@x.io', organization=self.org,
                                         role='buyer', buyer=self.bop, is_active=True)
        camp = Campaign.objects.create(organization=self.org, name='C-1')
        for i in range(2):
            CallLog.objects.create(organization=self.org, campaign=camp, caller_number='+15551234567',
                                   called_number='+18005550101', twilio_call_sid=f'CA622_{i}',
                                   status=CallLog.Status.COMPLETED, duration=30)

    def api(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def test_caller_profile_works_for_staff(self):
        r = self.api(self.admin).get('/api/analytics/caller-profile/+15551234567')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['profile_exists'], r.json()['total_calls']), (True, 2))
        r = self.api(self.admin).get('/api/analytics/caller-profile/+15550000000')
        self.assertEqual((r.status_code, r.json()['total_calls']), (200, 0))

    def test_caller_profile_is_not_a_partners(self):
        r = self.api(self.buyer).get('/api/analytics/caller-profile/+15551234567')
        self.assertEqual(r.status_code, 403)

    def test_a_record_not_of_this_account_is_not_found(self):
        other = str(uuid.uuid4())
        admin = self.api(self.admin)
        self.assertEqual(admin.get(f'/api/dni/pools/{other}/sessions').status_code, 404)
        self.assertEqual(admin.get(f'/api/webhooks/{other}/deliveries').status_code, 404)
