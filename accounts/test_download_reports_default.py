"""CH-449 (Sir, 31 Oct): a publisher or buyer cannot download reports unless
the main account switches Download Reports on, and once switched off it stays
off."""
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from accounts.partner_permissions import capabilities_for_partner
from accounts.permissions import Capability
from buyers.models import Buyer
from publishers.models import Publisher


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class DownloadReportsDefaultTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin',
                                    is_active=True, is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        self.pub = Publisher.objects.create(organization=self.org, name='ADST')
        self.buyer = Buyer.objects.create(organization=self.org, name='YNA')
        self.pub_login = User.objects.create(username='p', email='p@x.io', organization=self.org,
                                             role='publisher', publisher=self.pub, is_active=True)
        self.buyer_login = User.objects.create(username='b', email='b@x.io', organization=self.org,
                                               role='buyer', buyer=self.buyer, is_active=True)

    def url(self, kind, obj):
        return f'/api/accounts/{kind}/{obj.id}/settings'

    def test_nobody_decided_means_no_download(self):
        for kind, obj, login in (('publisher', self.pub, self.pub_login), ('buyer', self.buyer, self.buyer_login)):
            body = self.api.get(self.url(kind, obj)).json()
            self.assertFalse(body['permissions']['download_reports'], kind)
            self.assertNotIn(Capability.EXPORT, capabilities_for_partner(login), kind)

    def test_on_only_when_the_admin_switches_it_on_and_off_stays_off(self):
        url = self.url('publisher', self.pub)
        on = self.api.patch(url, {'permissions': {'download_reports': True}}, content_type='application/json')
        self.assertTrue(on.json()['permissions']['download_reports'])
        self.pub_login.refresh_from_db()
        self.assertIn(Capability.EXPORT, capabilities_for_partner(self.pub_login))
        self.api.patch(url, {'permissions': {'download_reports': False}}, content_type='application/json')
        # Saving another toggle without naming Download Reports does not bring it back.
        self.api.patch(url, {'permissions': {'block_numbers': True}}, content_type='application/json')
        self.pub.refresh_from_db()
        self.assertFalse(self.pub.permissions['download_reports'])
        self.assertFalse(self.api.get(url).json()['permissions']['download_reports'])
        self.pub_login.refresh_from_db()
        self.assertNotIn(Capability.EXPORT, capabilities_for_partner(self.pub_login))
