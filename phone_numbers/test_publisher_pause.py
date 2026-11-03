"""CH-492: a publisher login with Play/Pause TFNs on switches its own TFNs off
and on - nothing else, and never another publisher's.

Run:  python manage.py test phone_numbers.test_publisher_pause
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PublisherPauseTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO 2')
        other = Publisher.objects.create(organization=self.org, name='Other')
        self.login = User.objects.create(username='p', email='p@x.io', organization=self.org,
                                         role='publisher', publisher=self.pub, is_active=True)
        self.mine = PhoneNumber.objects.create(organization=self.org, number='+18005550001', vendor='other',
                                               publisher=self.pub, label='Mine')
        self.theirs = PhoneNumber.objects.create(organization=self.org, number='+18005550002', vendor='other',
                                                 publisher=other)

    def allow(self, on=True):
        self.pub.permissions = {'pause_numbers': on}
        self.pub.save()

    def patch(self, number, body):
        c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.login)}')
        return c.patch(f'/api/numbers/{number.id}', json.dumps(body), content_type='application/json')

    def test_off_by_default(self):
        self.assertEqual(self.patch(self.mine, {'status': 'paused'}).status_code, 403)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.status, 'active')

    def test_switches_its_own_tfn_off_and_on(self):
        self.allow()
        self.assertEqual(self.patch(self.mine, {'status': 'paused'}).status_code, 200)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.status, 'paused')
        self.assertEqual(self.patch(self.mine, {'status': 'active'}).status_code, 200)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.status, 'active')

    def test_nothing_else_and_never_another_publishers(self):
        self.allow()
        self.assertEqual(self.patch(self.mine, {'status': 'paused', 'label': 'x'}).status_code, 403)
        self.assertEqual(self.patch(self.mine, {'label': 'x'}).status_code, 403)
        self.assertEqual(self.patch(self.mine, {'status': 'released'}).status_code, 403)
        self.assertEqual(self.patch(self.theirs, {'status': 'paused'}).status_code, 404)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.status, 'active')

    def test_switch_is_in_the_catalogue(self):
        from accounts.partner_permissions import PERMISSION_KEYS, default_permissions
        self.assertIn('pause_numbers', PERMISSION_KEYS)
        self.assertFalse(default_permissions()['pause_numbers'])
