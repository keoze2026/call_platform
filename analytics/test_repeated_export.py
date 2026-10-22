"""CH-216: the export calls a duplicate caller "Repeated", Sir's word.

Run:  python manage.py test analytics.test_repeated_export
"""
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RepeatedExportTests(TestCase):
    def test_the_export_has_a_repeated_column(self):
        org = Organization.objects.create(name='Avortyx', slug='avx')
        owner = User.objects.create(username='o', email='o@x.io', organization=org,
                                    role='admin', is_superuser=True)
        CallRecord.objects.create(organization=org, caller_number='+15551234567',
                                  called_number='+18005550100', status='completed',
                                  is_duplicate=True, started_at=timezone.now())
        today = timezone.now().date().isoformat()
        r = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(owner)}').get(
            f'/api/analytics/calls/export?date_from={today}&date_to={today}&timezone=UTC')
        self.assertEqual(r.status_code, 200)
        body = b''.join(r.streaming_content).decode() if r.streaming else r.content.decode()
        header, row = body.splitlines()[:2]
        cols = header.split(',')
        self.assertIn('Repeated', cols)
        self.assertNotIn('Duplicate', cols)
        self.assertEqual(row.split(',')[cols.index('Repeated')], 'Yes')
