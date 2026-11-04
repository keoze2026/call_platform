"""CH-517: GET /api/health - public, 200 when the API and the database answer.

Run:  python manage.py test config.test_health
"""
from unittest import mock

from django.test import Client, TestCase, override_settings


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class HealthTests(TestCase):
    def get(self):
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
            return Client().get('/api/health')

    def test_up_without_signing_in(self):
        r = self.get()
        self.assertEqual((r.status_code, r.json()), (200, {'ok': True}))

    def test_database_down_is_503(self):
        with mock.patch('django.db.backends.utils.CursorWrapper.execute', side_effect=Exception('down')):
            r = self.get()
        self.assertEqual((r.status_code, r.json()), (503, {'ok': False}))
