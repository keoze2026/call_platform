"""CH-482: the header's dashboard poll is shared for a few seconds.

Run:  python manage.py test analytics.test_header_cache
"""
from unittest import mock

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.models import Buyer


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class HeaderCacheTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.a1 = User.objects.create(username='a1', email='a1@x.io', organization=self.org, role='admin')
        self.a2 = User.objects.create(username='a2', email='a2@x.io', organization=self.org, role='admin')
        b = Buyer.objects.create(organization=self.org, name='Q08')
        self.buyer = User.objects.create(username='b', email='b@x.io', organization=self.org,
                                         role='buyer', buyer=b)

    def get(self, user):
        c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')
        r = c.get('/api/analytics/dashboard')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_tabs_share_one_answer(self):
        from analytics.services import AnalyticsService
        real = AnalyticsService.get_dashboard
        with mock.patch.object(AnalyticsService, 'get_dashboard', side_effect=real) as spy:
            self.get(self.a1)
            self.get(self.a1)
            self.get(self.a2)
            self.assertEqual(spy.call_count, 1)
            # A buyer never gets the staff answer.
            self.get(self.buyer)
            self.assertEqual(spy.call_count, 2)
