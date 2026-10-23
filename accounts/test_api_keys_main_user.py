"""CH-243: API keys are seen and managed by the workspace's main account only.

Sir, 24 Oct: "Not allowed anyone - publisher, buyer, admin, manager - just the
main user of that account." Every login could list, make and revoke the
workspace's keys, a publisher or buyer included.

Run:  python manage.py test accounts.test_api_keys_main_user
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import APIKey, Organization, User
from accounts.services import APIKeyService
from buyers.models import Buyer
from publishers.models import Publisher

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
PATHS = ('/api/accounts/api-keys/', '/api/accounts/api-keys')


@override_settings(CACHES=CACHE)
class ApiKeysMainUserTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        now = timezone.now()
        self.main = User.objects.create(username='main', email='main@x.io', organization=self.org,
                                        role='admin', is_active=True, date_joined=now - timedelta(days=30))
        self.admin = User.objects.create(username='adm2', email='adm2@x.io', organization=self.org,
                                         role='admin', is_active=True, date_joined=now)
        self.manager = User.objects.create(username='mgr', email='mgr@x.io', organization=self.org,
                                           role='manager', is_active=True)
        pub = Publisher.objects.create(organization=self.org, name='PDSO 2')
        self.publisher = User.objects.create(username='pub', email='pub@x.io', organization=self.org,
                                             role='publisher', publisher=pub, is_active=True)
        buyer = Buyer.objects.create(organization=self.org, name='PDSO')
        self.buyer = User.objects.create(username='buy', email='buy@x.io', organization=self.org,
                                         role='buyer', buyer=buyer, is_active=True)
        self.bot_key = APIKeyService.create(self.buyer, 'PDSO Stats API')

    def api(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def call(self, user, method, path, **kw):
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
            return getattr(self.api(user), method)(path, content_type='application/json', **kw)

    def test_only_the_main_account_sees_the_keys(self):
        for path in PATHS:
            self.assertEqual(self.call(self.main, 'get', path).status_code, 200, path)
            for who in (self.admin, self.manager, self.publisher, self.buyer):
                r = self.call(who, 'get', path)
                self.assertEqual(r.status_code, 403, f'{who.role} {path}')
                self.assertNotIn('PDSO Stats API', r.content.decode())

    def test_only_the_main_account_makes_keys(self):
        for who in (self.admin, self.manager, self.publisher, self.buyer):
            for path in PATHS:
                r = self.call(who, 'post', path, data=json.dumps({'name': 'x'}))
                self.assertEqual(r.status_code, 403, f'{who.role} {path}')
        self.assertFalse(APIKey.objects.filter(name='x').exists())
        self.assertEqual(self.call(self.main, 'post', PATHS[0], data=json.dumps({'name': 'ok'})).status_code, 201)

    def test_nobody_else_can_revoke_a_key(self):
        key_id = self.bot_key['id'] if isinstance(self.bot_key, dict) else str(self.bot_key.id)
        for who in (self.admin, self.manager, self.publisher, self.buyer):
            self.assertEqual(self.call(who, 'delete', f'{PATHS[1]}/{key_id}').status_code, 403)
        self.assertTrue(APIKey.objects.get(id=key_id).is_active)
        self.assertEqual(self.call(self.main, 'delete', f'{PATHS[1]}/{key_id}').status_code, 200)

    def test_the_keys_keep_working(self):
        # A buyer bot's key still reads the API; only seeing and managing keys changed.
        from accounts.services import APIKeyService as S
        raw = self.bot_key['key']
        self.assertIsNotNone(S.validate(raw))

    def test_profile_says_who_the_main_account_is(self):
        self.assertTrue(self.call(self.main, 'get', '/api/accounts/me').json()['is_main_user'])
        self.assertFalse(self.call(self.admin, 'get', '/api/accounts/me').json()['is_main_user'])
        self.assertFalse(self.call(self.publisher, 'get', '/api/accounts/me').json()['is_main_user'])

    def test_the_platform_owner_may(self):
        owner = User.objects.create(username='own', email='own@x.io', organization=self.org,
                                    role='admin', is_active=True, is_superuser=True)
        self.assertEqual(self.call(owner, 'get', PATHS[0]).status_code, 200)
