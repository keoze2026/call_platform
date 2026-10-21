"""CH-185: a blocked caller is red in every account, and the main account's
Blocked Numbers list shows every block with who blocked it.

Run:  python manage.py test spam_protection.test_blocked_everywhere
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.models import Buyer
from campaigns.models import Campaign
from publishers.models import Publisher
from spam_protection.models import Blacklist

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=CACHE, ALLOWED_HOSTS=['*'])
class BlockedEverywhereTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Org', slug='org')
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO 2', permissions={'block_numbers': True})
        self.buyer = Buyer.objects.create(organization=self.org, name='PDSO')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-03')
        self.admin = User.objects.create(username='adm', email='adm@x.io', first_name='Main', last_name='Admin',
                                         organization=self.org, role='admin', is_active=True)
        self.pub_user = User.objects.create(username='pub', email='pub@x.io', organization=self.org,
                                            role='publisher', publisher=self.pub, is_active=True)
        self.buyer_user = User.objects.create(username='buy', email='buy@x.io', organization=self.org,
                                              role='buyer', buyer=self.buyer, is_active=True)

    def api(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def block(self, number, by, **scope):
        return Blacklist.objects.create(organization_id=self.org.id, phone_number=number, added_by=by, **scope)

    def red(self, user):
        r = self.api(user).get('/api/spam/blocked-callers')
        self.assertEqual(r.status_code, 200, r.content)
        return sorted(i['phone_number'] for i in r.json()['items'])

    def main_list(self):
        r = self.api(self.admin).get('/api/spam/blacklist')
        self.assertEqual(r.status_code, 200, r.content)
        return {i['phone_number']: i for i in r.json()['items']}

    # ---- red everywhere
    def test_every_login_sees_every_block(self):
        self.block('1111111111', self.admin)
        self.block('2222222222', self.pub_user, publisher=self.pub)
        self.block('3333333333', self.buyer_user, buyer=self.buyer)
        everything = ['1111111111', '2222222222', '3333333333']
        for user in (self.admin, self.pub_user, self.buyer_user):
            self.assertEqual(self.red(user), everything, user.role)

    def test_unblocked_and_expired_numbers_are_not_red(self):
        self.block('1111111111', self.admin, is_active=False)
        self.block('2222222222', self.admin, expires_at=timezone.now() - timedelta(hours=1))
        self.block('3333333333', self.admin, expires_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.red(self.pub_user), ['3333333333'])

    def test_a_campaign_block_keeps_its_campaign(self):
        self.block('1111111111', self.admin, campaign=self.campaign)
        r = self.api(self.pub_user).get('/api/spam/blocked-callers').json()['items'][0]
        self.assertEqual(r['campaign_id'], str(self.campaign.id))

    def test_another_workspace_never_shows(self):
        other = Organization.objects.create(name='Other', slug='other')
        Blacklist.objects.create(organization_id=other.id, phone_number='9999999999')
        self.assertEqual(self.red(self.admin), [])

    def test_a_viewer_without_the_blocked_numbers_tick_still_gets_red(self):
        viewer = User.objects.create(username='v', email='v@x.io', organization=self.org, role='viewer', is_active=True)
        self.block('1111111111', self.admin)
        with mock.patch('accounts.workspace_permissions.effective_permissions', return_value=['reporting.view']):
            self.assertEqual(self.red(viewer), ['1111111111'])

    # ---- the main list: every block, and who blocked it
    def test_main_list_shows_who_blocked_each_number(self):
        self.block('1111111111', self.admin)
        self.block('2222222222', self.pub_user, publisher=self.pub)
        self.block('3333333333', self.buyer_user, buyer=self.buyer)
        self.block('4444444444', self.admin, publisher=self.pub)   # main account, on the publisher's page
        rows = self.main_list()
        self.assertEqual((rows['1111111111']['blocked_by_type'], rows['1111111111']['blocked_by_name']), ('main', 'Main Admin'))
        self.assertEqual(rows['1111111111']['applies_to'], 'All calls')
        self.assertEqual((rows['2222222222']['blocked_by_type'], rows['2222222222']['blocked_by_name']), ('publisher', 'PDSO 2'))
        self.assertEqual(rows['2222222222']['applies_to'], 'Publisher PDSO 2 only')
        self.assertEqual((rows['3333333333']['blocked_by_type'], rows['3333333333']['blocked_by_name']), ('buyer', 'PDSO'))
        self.assertEqual(rows['3333333333']['applies_to'], 'Buyer PDSO only')
        self.assertEqual(rows['4444444444']['blocked_by_type'], 'main')
        self.assertEqual(rows['4444444444']['applies_to'], 'Publisher PDSO 2 only')

    def test_old_rows_without_a_login_fall_back_to_their_scope(self):
        self.block('2222222222', None, publisher=self.pub)
        self.block('3333333333', None, buyer=self.buyer)
        rows = self.main_list()
        self.assertEqual(rows['2222222222']['blocked_by_type'], 'publisher')
        self.assertEqual(rows['3333333333']['blocked_by_type'], 'buyer')

    def test_main_account_can_unblock_a_publisher_block(self):
        e = self.block('2222222222', self.pub_user, publisher=self.pub)
        self.assertEqual(self.api(self.admin).delete(f'/api/spam/blacklist/{e.id}').status_code, 200)
        self.assertFalse(Blacklist.objects.filter(pk=e.pk).exists())

    def test_partner_lists_stay_their_own(self):
        self.block('1111111111', self.admin)
        self.block('2222222222', self.pub_user, publisher=self.pub)
        r = self.api(self.pub_user).get('/api/spam/blacklist').json()['items']
        self.assertEqual([i['phone_number'] for i in r], ['2222222222'])
