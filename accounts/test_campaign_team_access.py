"""CH-478: the campaign team may do everything except delete TFNs or take them
off their campaign.

Run:  python manage.py test accounts.test_campaign_team_access
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import CustomRole, Organization, User
from accounts.workspace_permissions import ALL_KEYS
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class CampaignTeamTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='adm', email='adm@x.io', organization=self.org,
                                         role='admin', is_active=True)
        everything_but = [k for k in ALL_KEYS if k != 'tracking-numbers.delete']
        role = CustomRole.objects.create(organization=self.org, name='Campaign Team',
                                         capabilities=everything_but)
        self.team = User.objects.create(username='sergio', email='sergio@t.io', organization=self.org,
                                        role=str(role.id), is_active=True)
        viewer = CustomRole.objects.create(organization=self.org, name='Look only',
                                           capabilities=['buyers.view'])
        self.viewer = User.objects.create(username='v', email='v@t.io', organization=self.org,
                                          role=str(viewer.id), is_active=True)
        self.buyer = Buyer.objects.create(organization=self.org, name='A49')
        self.buyer_login = User.objects.create(username='a49', email='a49@b.io', organization=self.org,
                                               role='buyer', buyer=self.buyer, is_active=True)
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO 2')
        self.c1 = Campaign.objects.create(organization=self.org, name='One')
        self.c2 = Campaign.objects.create(organization=self.org, name='Two')
        self.attached = PhoneNumber.objects.create(organization=self.org, number='+18005550001',
                                                   vendor='other', campaign=self.c1)
        self.loose = PhoneNumber.objects.create(organization=self.org, number='+18005550002', vendor='other')

    def api(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def patch(self, user, path, body):
        return self.api(user).patch(path, json.dumps(body), content_type='application/json')

    # ── buyer and publisher access ──────────────────────────────────────────
    def test_team_switches_set_caps_on_for_a_buyer(self):
        r = self.patch(self.team, f'/api/accounts/buyer/{self.buyer.id}/settings',
                       {'permissions': {'set_caps': True}})
        self.assertEqual(r.status_code, 200, r.content)
        self.buyer.refresh_from_db()
        self.assertTrue(self.buyer.permissions.get('set_caps'))

    def test_team_saves_the_buyer_access_card_and_publisher_settings(self):
        self.assertEqual(self.api(self.team).get(f'/api/buyers/{self.buyer.id}/access').status_code, 200)
        r = self.patch(self.team, f'/api/buyers/{self.buyer.id}/access',
                       {'permissions': {'download_reports': True}})
        self.assertEqual(r.status_code, 200, r.content)
        r = self.patch(self.team, f'/api/accounts/publisher/{self.pub.id}/settings',
                       {'permissions': {'block_numbers': True}})
        self.assertEqual(r.status_code, 200, r.content)

    def test_without_the_edit_tick_still_refused(self):
        r = self.patch(self.viewer, f'/api/accounts/buyer/{self.buyer.id}/settings',
                       {'permissions': {'set_caps': True}})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.api(self.viewer).get(f'/api/buyers/{self.buyer.id}/access').status_code, 200)
        self.assertEqual(self.patch(self.viewer, f'/api/buyers/{self.buyer.id}/access',
                                    {'permissions': {'download_reports': True}}).status_code, 403)

    def test_a_partner_login_never_changes_access(self):
        self.buyer.permissions = {'manage_traffic': True}
        self.buyer.save()
        for path, body in ((f'/api/accounts/buyer/{self.buyer.id}/settings', {'permissions': {'set_caps': True}}),
                           (f'/api/buyers/{self.buyer.id}/access', {'permissions': {'download_reports': True}})):
            self.assertEqual(self.patch(self.buyer_login, path, body).status_code, 403, path)
        self.assertEqual(self.api(self.buyer_login).get(f'/api/buyers/{self.buyer.id}/access').status_code, 403)

    # ── TFNs: everything but delete and remove ───────────────────────────────
    def test_team_cannot_delete_a_tfn(self):
        r = self.api(self.team).delete(f'/api/numbers/{self.attached.id}/release')
        self.assertEqual(r.status_code, 403)

    def test_team_cannot_take_a_tfn_off_its_campaign(self):
        for body in ({'campaign_id': None}, {'campaign_id': str(self.c2.id)}, {'status': 'released'}):
            r = self.patch(self.team, f'/api/numbers/{self.attached.id}', body)
            self.assertEqual(r.status_code, 403, body)
            self.assertIn('Tracking Numbers: Delete', r.json()['detail'])
        r = self.api(self.team).post(f'/api/numbers/{self.attached.id}/assign',
                                     json.dumps({'campaign_id': str(self.c2.id)}), content_type='application/json')
        self.assertEqual(r.status_code, 403)
        self.attached.refresh_from_db()
        self.assertEqual(self.attached.campaign_id, self.c1.id)

    def test_team_attaches_and_edits_tfns(self):
        r = self.patch(self.team, f'/api/numbers/{self.loose.id}', {'campaign_id': str(self.c1.id)})
        self.assertEqual(r.status_code, 200, r.content)
        r = self.patch(self.team, f'/api/numbers/{self.attached.id}', {'label': 'New name'})
        self.assertEqual(r.status_code, 200, r.content)
        # Re-sending the same campaign is not a removal.
        r = self.patch(self.team, f'/api/numbers/{self.attached.id}', {'campaign_id': str(self.c1.id)})
        self.assertEqual(r.status_code, 200, r.content)
        self.loose.refresh_from_db()
        self.assertEqual(self.loose.campaign_id, self.c1.id)

    def test_admin_still_removes(self):
        r = self.patch(self.admin, f'/api/numbers/{self.attached.id}', {'campaign_id': None})
        self.assertEqual(r.status_code, 200, r.content)
        self.attached.refresh_from_db()
        self.assertIsNone(self.attached.campaign_id)
