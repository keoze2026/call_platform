"""CH-649 (Sir, 9 Nov: "Live call should also come in excel with 0 sec until its
live"). The screen's total is finished calls plus live ones; the export now
lists both - live calls first, "In progress", 0 seconds, no money - so the
file's row count equals the total on screen (1,176 on screen vs 1,135 rows on
9 Nov: 41 calls were live).

Run:  python manage.py test analytics.test_live_in_export
"""
import csv
import io
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from routing.models import CallLog


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class LiveCallsInExportTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(username='o', email='o@x.io', organization=self.org,
                                         role='admin', is_superuser=True)
        CallRecord.objects.create(organization=self.org, caller_number='+15551234567',
                                  called_number='+18005550100', status='completed',
                                  duration_seconds=90, started_at=timezone.now())

    def export(self):
        today = timezone.now().date().isoformat()
        r = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}').get(
            f'/api/analytics/calls/export?date_from={today}&date_to={today}&timezone=UTC')
        self.assertEqual(r.status_code, 200)
        body = b''.join(r.streaming_content).decode() if r.streaming else r.content.decode()
        rows = list(csv.reader(io.StringIO(body)))
        return rows[0], rows[1:]

    def live(self, sid, **kw):
        return CallLog.objects.create(organization=self.org, caller_number='+15559876543',
                                      called_number='+18005550101', twilio_call_sid=sid,
                                      status='in_progress', **kw)

    def test_a_live_call_is_in_the_file_with_0_seconds(self):
        c = self.live('s1')
        header, rows = self.export()
        self.assertEqual(len(rows), 2)                                  # 1 finished + 1 live
        first = dict(zip(header, rows[0]))
        self.assertEqual(first['Call ID'], str(c.id))                   # live first
        self.assertEqual(first['Status'], 'In progress')
        self.assertEqual(first['Duration (s)'], '0')
        self.assertEqual((first['Revenue'], first['Payout'], first['Cost']), ('0', '0', '0'))
        self.assertEqual(dict(zip(header, rows[1]))['Duration (s)'], '90')

    def test_only_calls_that_are_live_now(self):
        # Ended, or stuck for over 4 hours: not live (the same rule as the screen).
        self.live('s2', ended_at=timezone.now())
        old = self.live('s3')
        CallLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=5))
        header, rows = self.export()
        self.assertEqual([dict(zip(header, r))['Status'] for r in rows].count('In progress'), 0)

    def test_a_status_filter_other_than_live_leaves_them_out(self):
        self.live('s4')
        today = timezone.now().date().isoformat()
        r = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}').get(
            f'/api/analytics/calls/export?date_from={today}&date_to={today}&timezone=UTC&status=completed')
        body = b''.join(r.streaming_content).decode() if r.streaming else r.content.decode()
        self.assertNotIn('In progress', body)
