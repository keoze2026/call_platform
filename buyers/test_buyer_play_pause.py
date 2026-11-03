"""CH-475 (Sir, 3 Nov): "Buyers can play pause their TFNs - only buyer access".

A buyer login may switch its own lines on and off, one at a time or many at
once, without any other right. It cannot change anything else on a line, and
it cannot touch another buyer's line - with or without the Manage Traffic
toggle. Staff are unchanged.

Run:  python manage.py test buyers.test_buyer_play_pause
"""
import json

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class BuyerPlayPauseTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.a49 = Buyer.objects.create(organization=self.org, name='A49')
        self.other = Buyer.objects.create(organization=self.org, name='OTHER')
        self.own = Destination.objects.create(organization=self.org, buyer=self.a49, name='A49 1',
                                              tfn='+18005550101', enabled=True)
        self.own2 = Destination.objects.create(organization=self.org, buyer=self.a49, name='A49 2',
                                               tfn='+18005550102', enabled=False)
        self.theirs = Destination.objects.create(organization=self.org, buyer=self.other, name='OTHER 1',
                                                 tfn='+18005550201', enabled=True)
        self.login = User.objects.create(username='a49', email='a49@x.io', organization=self.org,
                                         role='buyer', buyer=self.a49)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.login)}')

    def patch(self, d, body, api=None):
        return (api or self.api).patch(f'/api/destinations/{d.id}/', json.dumps(body),
                                       content_type='application/json')

    def bulk(self, ids, enabled, api=None):
        return (api or self.api).post('/api/destinations/bulk-enable', json.dumps(
            {'ids': [str(i) for i in ids], 'enabled': enabled}), content_type='application/json')

    def test_pause_and_play_own_line(self):
        self.assertEqual(self.patch(self.own, {'enabled': False}).status_code, 200)
        self.own.refresh_from_db()
        self.assertFalse(self.own.enabled)
        self.assertEqual(self.patch(self.own, {'enabled': True}).status_code, 200)
        self.own.refresh_from_db()
        self.assertTrue(self.own.enabled)

    def test_nothing_else_on_the_line(self):
        for body in ({'name': 'renamed'}, {'concurrency_cap': 99}, {'enabled': False, 'daily_cap': 5},
                     {'tfn': '+18005559999'}):
            self.assertEqual(self.patch(self.own, body).status_code, 403, body)
        self.own.refresh_from_db()
        self.assertEqual((self.own.name, self.own.enabled, self.own.tfn), ('A49 1', True, '+18005550101'))

    def test_never_another_buyers_line(self):
        self.assertEqual(self.patch(self.theirs, {'enabled': False}).status_code, 404)
        r = self.bulk([self.own2.id, self.theirs.id], False).json()
        self.assertEqual(r['updated'], [str(self.own2.id)])
        self.assertEqual(r['failed'], [{'id': str(self.theirs.id), 'reason': 'not found'}])
        self.theirs.refresh_from_db()
        self.assertTrue(self.theirs.enabled)

    def test_bulk_play_own_lines(self):
        r = self.bulk([self.own.id, self.own2.id], True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Destination.objects.filter(buyer=self.a49, enabled=True).count(), 2)

    def test_manage_traffic_still_only_own_lines(self):
        self.a49.permissions = dict(self.a49.permissions or {}, manage_traffic=True)
        self.a49.save()
        self.assertEqual(self.patch(self.own, {'name': 'renamed'}).status_code, 200)
        self.assertEqual(self.patch(self.theirs, {'name': 'taken'}).status_code, 404)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.name, 'OTHER 1')

    def test_staff_unchanged(self):
        admin = User.objects.create(username='m', email='m@x.io', organization=self.org, role='admin',
                                    is_superuser=True)
        api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        self.assertEqual(self.patch(self.theirs, {'name': 'by staff', 'enabled': False}, api).status_code, 200)
        viewer = User.objects.create(username='v', email='v@x.io', organization=self.org, role='viewer')
        vapi = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(viewer)}')
        self.assertEqual(self.patch(self.own, {'enabled': False}, vapi).status_code, 403)
        self.assertEqual(self.bulk([self.own.id], False, vapi).status_code, 403)
