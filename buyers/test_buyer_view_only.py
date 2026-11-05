"""CH-612 (Sir, 6 Nov): "Under buyer access - buyer cannot pause - play - set cc
from his own buyer cap section, he can just view. Buyer can only change cc to
his allotted destination, not the buyer section."

A buyer login - even with every partner switch on - views its buyer and changes
nothing on it: no pause, no play, no concurrency or caps, no edit, no delete, no
campaign links. Its own destinations keep their own switches. Staff unchanged.

Run:  python manage.py test buyers.test_buyer_view_only
"""
import json

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts import partner_permissions as P
from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer

ALL_ON = {k: True for k in (P.MANAGE_TRAFFIC, P.NUMBER_CREATION, P.EDIT_DESTINATIONS,
                            P.PAUSE_DESTINATIONS, P.SET_CONCURRENCY, P.SET_CAPS)}


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class BuyerViewOnlyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.bop = Buyer.objects.create(organization=self.org, name='BOP', status='active',
                                        permissions=ALL_ON)
        self.line = Destination.objects.create(organization=self.org, buyer=self.bop, name='BOP 1',
                                               tfn='+18005550101', enabled=True, concurrency_cap=5)
        self.login = User.objects.create(username='bop', email='bop@x.io', organization=self.org,
                                         role='buyer', buyer=self.bop)
        self.admin = User.objects.create(username='adm', email='adm@x.io', organization=self.org,
                                         role='admin')
        self.buyer_api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.login)}')
        self.admin_api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')

    def calls(self, api):
        b = f'/api/buyers/{self.bop.id}'
        j = 'application/json'
        return {
            'pause': api.post(f'{b}/pause'),
            'activate': api.post(f'{b}/activate'),
            'concurrency': api.patch(f'{b}/cap', json.dumps({'max_concurrency': 3}), content_type=j),
            'edit': api.patch(b, json.dumps({'name': 'BOP2'}), content_type=j),
            'delete': api.delete(b),
        }

    def test_a_buyer_login_only_views_its_buyer(self):
        self.assertEqual(self.buyer_api.get(f'/api/buyers/{self.bop.id}').status_code, 200)
        for name, r in self.calls(self.buyer_api).items():
            self.assertEqual(r.status_code, 403, name)
        self.bop.refresh_from_db()
        self.assertEqual((self.bop.name, self.bop.status), ('BOP', 'active'))

    def test_it_still_sets_concurrency_on_its_own_destination(self):
        r = self.buyer_api.patch(f'/api/destinations/{self.line.id}/', json.dumps({'concurrency_cap': 3}),
                                 content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.line.refresh_from_db()
        self.assertEqual(self.line.concurrency_cap, 3)

    def test_staff_unchanged(self):
        self.assertEqual(self.admin_api.post(f'/api/buyers/{self.bop.id}/pause').status_code, 200)
        self.assertEqual(self.admin_api.post(f'/api/buyers/{self.bop.id}/activate').status_code, 200)
