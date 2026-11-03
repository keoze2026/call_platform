"""CH-477: Active Bots - every buyer's Stats API key on one page.

Run:  python manage.py test accounts.test_active_bots
"""
import hashlib
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import ActivityLog, APIKey, Organization, User
from accounts.services import APIKeyService
from buyers.models import Buyer

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
BASE = '/api/active-bots'


@override_settings(CACHES=CACHE)
class ActiveBotsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        now = timezone.now()
        self.main = User.objects.create(username='main', email='main@x.io', organization=self.org,
                                        role='admin', is_active=True, date_joined=now - timedelta(days=30))
        self.admin = User.objects.create(username='adm2', email='adm2@x.io', organization=self.org,
                                         role='admin', is_active=True, date_joined=now)
        self.manager = User.objects.create(username='mgr', email='mgr@x.io', organization=self.org,
                                           role='manager', is_active=True)
        self.q08 = Buyer.objects.create(organization=self.org, name='Q08')
        self.a49 = Buyer.objects.create(organization=self.org, name='A49')
        self.buyer_login = User.objects.create(username='a49', email='a49@buyer.com', organization=self.org,
                                               role='buyer', buyer=self.a49, is_active=True)
        self.bot = User.objects.create(username='q08bot@avortyx.io', email='q08bot@avortyx.io',
                                       organization=self.org, role='buyer', buyer=self.q08, is_active=True)
        self.old = APIKeyService.create(self.bot, 'Q08 Stats API')
        APIKey.objects.filter(id=self.old['id']).update(last_used_at=now - timedelta(minutes=5))
        # Another workspace's buyer never shows.
        other = Organization.objects.create(name='Other', slug='oth')
        self.foreign = Buyer.objects.create(organization=other, name='Q08')

    def call(self, user, method, path):
        c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
            return getattr(c, method)(path, content_type='application/json')

    def rows(self):
        r = self.call(self.main, 'get', BASE + '/')
        self.assertEqual(r.status_code, 200)
        return {i['buyer_name']: i for i in r.json()['items']}

    def test_main_account_only(self):
        for who in (self.admin, self.manager, self.buyer_login, self.bot):
            self.assertEqual(self.call(who, 'get', BASE + '/').status_code, 403, who.role)
            self.assertEqual(self.call(who, 'post', f'{BASE}/{self.q08.id}/issue').status_code, 403)
            self.assertEqual(self.call(who, 'post', f'{BASE}/{self.q08.id}/switch-off').status_code, 403)
        self.assertTrue(APIKey.objects.get(id=self.old['id']).is_active)

    def test_list_shows_every_buyer_never_the_key(self):
        r = self.call(self.main, 'get', BASE + '/')
        self.assertNotIn(self.old['key'], r.content.decode())
        rows = self.rows()
        self.assertEqual(set(rows), {'Q08', 'A49'})
        q = rows['Q08']
        self.assertEqual(q['status'], 'active')
        self.assertEqual(q['login_email'], 'q08bot@avortyx.io')
        self.assertEqual(q['key_prefix'], self.old['key_prefix'])
        self.assertIsNotNone(q['last_used_at'])
        a = rows['A49']
        self.assertEqual(a['status'], 'none')
        self.assertFalse(a['login_exists'])
        self.assertEqual(a['login_email'], 'a49bot@avortyx.io')
        self.assertEqual(r.json()['counts'], {'active': 1, 'off': 0, 'none': 1})

    def test_issue_replaces_the_old_key_and_shows_the_new_one_once(self):
        r = self.call(self.main, 'post', f'{BASE}/{self.q08.id}/issue')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['old_keys_switched_off'], 1)
        self.assertEqual(body['login_email'], 'q08bot@avortyx.io')
        self.assertFalse(body['login_created'])
        self.assertTrue(body['key'].startswith('sk_live_'))
        self.assertFalse(APIKey.objects.get(id=self.old['id']).is_active)
        self.assertIsNone(APIKeyService.validate(self.old['key']))
        new = APIKeyService.validate(body['key'])
        self.assertEqual(new.user, self.bot)
        # Stored as a hash only; the list shows the first characters.
        self.assertEqual(new.key_hash, hashlib.sha256(body['key'].encode()).hexdigest())
        listed = self.call(self.main, 'get', BASE + '/').content.decode()
        self.assertNotIn(body['key'], listed)
        self.assertEqual(self.rows()['Q08']['key_prefix'], body['key_prefix'])
        self.assertTrue(ActivityLog.objects.filter(user=self.main, action='api_key_created',
                                                   metadata__buyer='Q08').exists())

    def test_issue_for_a_buyer_without_a_bot_makes_the_login(self):
        r = self.call(self.main, 'post', f'{BASE}/{self.a49.id}/issue')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body['login_created'])
        bot = User.objects.get(email='a49bot@avortyx.io')
        self.assertEqual((bot.role, bot.buyer, bot.organization), ('buyer', self.a49, self.org))
        self.assertFalse(bot.has_usable_password())
        self.assertEqual(APIKeyService.validate(body['key']).user, bot)
        # The buyer's own portal login is untouched and holds no key.
        self.assertFalse(APIKey.objects.filter(user=self.buyer_login).exists())
        # A second issue reuses the same login.
        again = self.call(self.main, 'post', f'{BASE}/{self.a49.id}/issue').json()
        self.assertFalse(again['login_created'])
        self.assertEqual(again['old_keys_switched_off'], 1)
        self.assertEqual(User.objects.filter(buyer=self.a49).count(), 2)

    def test_taken_bot_email_gets_a_number(self):
        User.objects.create(username='a49bot@avortyx.io', email='a49bot@avortyx.io',
                            organization=Organization.objects.create(name='O2', slug='o2'), role='buyer')
        body = self.call(self.main, 'post', f'{BASE}/{self.a49.id}/issue').json()
        self.assertEqual(body['login_email'], 'a49bot2@avortyx.io')

    def test_switch_off(self):
        r = self.call(self.main, 'post', f'{BASE}/{self.q08.id}/switch-off')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['keys_switched_off'], 1)
        self.assertIsNone(APIKeyService.validate(self.old['key']))
        q = self.rows()['Q08']
        self.assertEqual(q['status'], 'off')
        self.assertEqual(q['login_email'], 'q08bot@avortyx.io')
        self.assertTrue(ActivityLog.objects.filter(user=self.main, action='api_key_revoked',
                                                   metadata__buyer='Q08').exists())

    def test_other_workspace_buyer_is_not_found(self):
        self.assertEqual(self.call(self.main, 'post', f'{BASE}/{self.foreign.id}/issue').status_code, 404)
        self.assertEqual(self.call(self.main, 'post', f'{BASE}/not-a-uuid/switch-off').status_code, 404)

    def test_archived_buyer_without_key_is_hidden(self):
        Buyer.objects.create(organization=self.org, name='Gone', status='archived')
        self.assertNotIn('Gone', self.rows())


@override_settings(CACHES=CACHE)
class CopyKeyTests(ActiveBotsTests):
    """CH-490: a key issued on Active Bots can be copied again by the main
    account; older keys cannot."""

    def test_issued_key_can_be_copied_again(self):
        issued = self.call(self.main, 'post', f'{BASE}/{self.q08.id}/issue').json()
        self.assertTrue(self.rows()['Q08']['copyable'])
        r = self.call(self.main, 'post', f'{BASE}/{self.q08.id}/copy')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['key'], issued['key'])
        self.assertTrue(ActivityLog.objects.filter(user=self.main, action='api_key_copied',
                                                   metadata__buyer='Q08').exists())
        # Stored encrypted, never as the key itself.
        k = APIKey.objects.get(key_prefix=issued['key_prefix'], is_active=True)
        self.assertNotIn(issued['key'], k.key_sealed)
        # The list never carries it.
        self.assertNotIn(issued['key'], self.call(self.main, 'get', BASE + '/').content.decode())

    def test_old_key_cannot_be_copied(self):
        self.assertFalse(self.rows()['Q08']['copyable'])
        r = self.call(self.main, 'post', f'{BASE}/{self.q08.id}/copy')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Issue a new key', r.json()['detail'])

    def test_only_the_main_account_copies(self):
        self.call(self.main, 'post', f'{BASE}/{self.q08.id}/issue')
        for who in (self.admin, self.manager, self.buyer_login, self.bot):
            self.assertEqual(self.call(who, 'post', f'{BASE}/{self.q08.id}/copy').status_code, 403, who.role)

    def test_switched_off_key_cannot_be_copied(self):
        self.call(self.main, 'post', f'{BASE}/{self.q08.id}/issue')
        self.call(self.main, 'post', f'{BASE}/{self.q08.id}/switch-off')
        self.assertEqual(self.call(self.main, 'post', f'{BASE}/{self.q08.id}/copy').status_code, 400)
