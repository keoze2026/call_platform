"""CH-462: today's report comes from the cache while its calls stay the same.

Run:  python manage.py test analytics.test_report_cache_today
"""
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics import report_cache
from analytics.models import CallRecord
from routing.models import CallLog


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class TodayReportCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        owner = User.objects.create(username='o', email='o@x.io', organization=self.org,
                                    role='admin', is_superuser=True)
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(owner)}')
        self.today = timezone.now().date().isoformat()
        # The stamp is kept a few seconds in production; here every request
        # reads it again, so a change is seen at once.
        patcher = mock.patch.object(report_cache, 'STAMP_TTL_SECONDS', 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, endpoint='calls'):
        r = self.client.get(f'/api/analytics/{endpoint}?date_from={self.today}'
                            f'&date_to={self.today}&timezone=UTC&limit=500')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def record(self, **kw):
        fields = dict(organization=self.org, caller_number='+15551234567',
                      called_number='+18005550100', status='completed',
                      duration_seconds=60, started_at=timezone.now())
        fields.update(kw)
        return CallRecord.objects.create(**fields)

    def test_a_reload_with_nothing_new_is_not_recomputed(self):
        self.record()
        from analytics.services import AnalyticsService
        with mock.patch.object(AnalyticsService, 'get_call_log',
                               wraps=AnalyticsService.get_call_log) as run:
            first = self.get()
            second = self.get()
        self.assertEqual(first, second)
        self.assertEqual(run.call_count, 1)

    def test_every_summary_is_cached_as_well(self):
        self.record()
        from analytics.services import AnalyticsService
        for endpoint, fn in (('campaigns', 'get_campaign_performance'),
                             ('buyers', 'get_buyer_performance'),
                             ('publishers', 'get_publisher_performance'),
                             ('carriers', 'get_carrier_performance')):
            with mock.patch.object(AnalyticsService, fn,
                                   wraps=getattr(AnalyticsService, fn)) as run:
                self.get(endpoint)
                self.get(endpoint)
            self.assertEqual(run.call_count, 1, endpoint)

    def test_a_new_call_shows_on_the_next_request(self):
        self.record()
        self.assertEqual(self.get()['total'], 1)
        self.record()
        self.assertEqual(self.get()['total'], 2)

    def test_a_call_ringing_now_shows_on_the_next_request(self):
        self.assertEqual(self.get()['total'], 0)
        CallLog.objects.create(organization=self.org, caller_number='+15551234567',
                               called_number='+18005550100', status='ringing')
        self.assertEqual(self.get()['total'], 1)

    def test_a_fee_written_without_save_shows_on_the_next_request(self):
        call = self.record(platform_cost=Decimal('0'))
        self.get()
        # How tasks.py charges a rejected call: .update(), updated_at untouched.
        CallRecord.objects.filter(pk=call.pk).update(platform_cost=Decimal('0.25'))
        cost = self.get()['items'][0]['cost']
        self.assertEqual(Decimal(str(cost)), Decimal('0.25'))

    def test_today_is_kept_a_minute_not_six_hours(self):
        self.record()
        with mock.patch.object(report_cache.cache, 'set', wraps=report_cache.cache.set) as put:
            self.get()
        report_ttls = [c.args[2] for c in put.call_args_list if c.args[0].startswith('reports:v4')]
        self.assertEqual(report_ttls, [report_cache.TODAY_TTL_SECONDS])

    def test_a_cache_that_cannot_be_read_still_gives_the_report(self):
        self.record()
        down = mock.Mock()
        down.get.side_effect = ConnectionError('redis down')
        down.set.side_effect = ConnectionError('redis down')
        with mock.patch.object(report_cache, 'cache', down):
            self.assertEqual(self.get()['total'], 1)
