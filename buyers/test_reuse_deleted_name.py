"""CH-494: a deleted buyer's name can be used again.

Run:  python manage.py test buyers.test_reuse_deleted_name
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ReuseDeletedNameTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin')
        self.c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')

    def create(self, name):
        return self.c.post('/api/buyers/', json.dumps({'name': name}),
                           content_type='application/json')

    def test_deleted_buyer_gives_its_name_up(self):
        old = Buyer.objects.create(organization=self.org, name='ZZY', status='archived')
        line = Destination.objects.create(organization=self.org, buyer=old, name='ZZY|1', tfn='+18005550100')
        r = self.create('ZZY')
        self.assertEqual(r.status_code, 201, r.content)
        old.refresh_from_db()
        self.assertTrue(old.name.startswith('ZZY (deleted '))
        self.assertEqual(old.status, 'archived')
        new = Buyer.objects.get(organization=self.org, name='ZZY')
        self.assertNotEqual(new.id, old.id)
        self.assertEqual(new.status, 'active')
        # The old buyer keeps its lines; the new one starts empty.
        line.refresh_from_db()
        self.assertEqual(line.buyer_id, old.id)

    def test_deleted_twice(self):
        Buyer.objects.create(organization=self.org, name='ZZY', status='archived')
        self.assertEqual(self.create('ZZY').status_code, 201)
        Buyer.objects.filter(name='ZZY').update(status='archived')
        self.assertEqual(self.create('zzy').status_code, 201)
        self.assertEqual(Buyer.objects.filter(organization=self.org, name__istartswith='ZZY (deleted').count(), 2)

    def test_a_live_buyer_still_blocks_the_name(self):
        Buyer.objects.create(organization=self.org, name='ZZY', status='paused')
        r = self.create('ZZY')
        self.assertEqual(r.status_code, 400)
        self.assertIn('already exists', r.json()['detail'])
