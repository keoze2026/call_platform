"""CH-260: buyers and publishers follow the portal's time zone (EST) unless
they pick their own (Sir, 25 Oct)."""
from types import SimpleNamespace

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.services import _tz
from buyers.models import Buyer
from publishers.models import Publisher


@override_settings(ALLOWED_HOSTS=['*'], PORTAL_TIMEZONE='America/New_York',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PartnerTimezoneTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin', is_active=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        self.buyer = Buyer.objects.create(organization=self.org, name='EKU')
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO')

    def login(self, **kw):
        u = User.objects.create(username=kw.get('role'), email=f"{kw.get('role')}@x.io", organization=self.org,
                                is_active=True, **kw)
        return u, Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')

    def test_a_new_buyer_shows_the_portal_zone(self):
        r = self.api.get(f'/api/buyers/{self.buyer.id}/access').json()
        self.assertEqual((r['timezone'], r['timezone_is_own']), ('America/New_York', False))

    def test_only_an_own_choice_changes_it_and_picking_est_follows_the_portal_again(self):
        url = f'/api/buyers/{self.buyer.id}/access'
        r = self.api.patch(url, {'timezone': 'America/Chicago'}, content_type='application/json').json()
        self.assertEqual((r['timezone'], r['timezone_is_own']), ('America/Chicago', True))
        self.api.patch(url, {'timezone': 'America/New_York'}, content_type='application/json')
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.timezone, '')
        self.assertEqual(self.api.patch(url, {'timezone': 'Mars/Base'}, content_type='application/json').status_code, 400)

    def test_a_buyer_login_counts_its_days_in_est_by_default(self):
        u, _ = self.login(role='buyer', buyer=self.buyer)
        self.assertEqual(str(_tz(SimpleNamespace(timezone=None), u)), 'America/New_York')
        Buyer.objects.filter(pk=self.buyer.pk).update(timezone='America/Los_Angeles')
        u.refresh_from_db()
        self.assertEqual(str(_tz(SimpleNamespace(timezone=None), u)), 'America/Los_Angeles')

    def test_publishers_keep_their_zone_on_the_server(self):
        url = f'/api/accounts/publisher/{self.pub.id}/settings'
        self.assertEqual(self.api.get(url).json()['timezone'], 'America/New_York')
        r = self.api.patch(url, {'timezone': 'Europe/London'}, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.api.get(url).json()['timezone'], 'Europe/London')
        u, me = self.login(role='publisher', publisher=self.pub)
        u = User.objects.get(pk=u.pk)   # a fresh login, as a request would load it
        self.assertEqual(str(_tz(SimpleNamespace(timezone=None), u)), 'Europe/London')

    def test_a_partner_login_reads_only_its_own_settings(self):
        other = Buyer.objects.create(organization=self.org, name='Q05')
        _, me = self.login(role='buyer', buyer=self.buyer)
        self.assertEqual(me.get(f'/api/accounts/buyer/{self.buyer.id}/settings').status_code, 200)
        self.assertEqual(me.get(f'/api/accounts/buyer/{other.id}/settings').status_code, 404)
        self.assertEqual(me.get(f'/api/accounts/publisher/{self.pub.id}/settings').status_code, 404)
