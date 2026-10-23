"""CH-219: Reports keep themselves current with auto refresh Off.

Run:  python manage.py test analytics.test_report_stamp
"""
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from routing.models import CallLog


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ReportStampTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        owner = User.objects.create(username='o', email='o@x.io', organization=self.org,
                                    role='admin', is_superuser=True)
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(owner)}')
        self.today = timezone.now().date().isoformat()

    def stamp(self, day=None):
        day = day or self.today
        r = self.client.get(f'/api/analytics/report-stamp?date_from={day}&date_to={day}&timezone=UTC')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def record(self, **kw):
        fields = dict(organization=self.org, caller_number='+15551234567',
                      called_number='+18005550100', status='no_answer',
                      started_at=timezone.now())
        fields.update(kw)
        return CallRecord.objects.create(**fields)

    def test_the_answer_has_a_stamp_and_the_server_time(self):
        body = self.stamp()
        self.assertEqual(len(body['stamp']), 16)
        self.assertTrue(body['as_of'])
        self.assertEqual(body['calls'], 0)

    def test_the_stamp_holds_while_nothing_changes(self):
        self.record()
        self.assertEqual(self.stamp()['stamp'], self.stamp()['stamp'])

    def test_a_new_call_changes_it(self):
        before = self.stamp()['stamp']
        self.record()
        after = self.stamp()
        self.assertNotEqual(before, after['stamp'])
        self.assertEqual(after['calls'], 1)

    def test_a_call_that_is_answered_changes_it(self):
        call = self.record(status='in_progress')
        before = self.stamp()['stamp']
        call.status = 'completed'
        call.duration_seconds = 75
        call.save()
        self.assertNotEqual(before, self.stamp()['stamp'])

    def test_a_call_that_leaves_the_report_changes_it(self):
        call = self.record()
        before = self.stamp()['stamp']
        call.delete()
        self.assertNotEqual(before, self.stamp()['stamp'])

    def test_another_days_call_does_not(self):
        before = self.stamp()['stamp']
        self.record(started_at=timezone.now() - timedelta(days=3))
        self.assertEqual(before, self.stamp()['stamp'])

    def test_a_call_ringing_now_changes_it(self):
        before = self.stamp()
        CallLog.objects.create(organization=self.org, caller_number='+15551234567',
                               called_number='+18005550100', status='ringing')
        after = self.stamp()
        self.assertNotEqual(before['stamp'], after['stamp'])
        self.assertEqual(after['live'], 1)
