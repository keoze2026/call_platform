"""CH-620 (Sir, 6 Nov): "Like we did under Buyer access - buyer cannot pause
from Buyer option. Same push for publisher - publisher cannot pause, publisher
access."

A publisher login - even with every partner switch on - views its publisher and
changes nothing on it: no pause, no play, no edit, no caps, no delete, no
campaign links. Staff unchanged.

Run:  python manage.py test publishers.test_publisher_view_only
"""
import json

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts import partner_permissions as P
from accounts.models import Organization, User
from campaigns.models import Campaign
from publishers.models import Publisher

ALL_ON = {k: True for k in (P.MANAGE_TRAFFIC, P.NUMBER_CREATION, P.EDIT_DESTINATIONS,
                            P.PAUSE_DESTINATIONS, P.SET_CONCURRENCY, P.SET_CAPS)}


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PublisherViewOnlyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.ds = Publisher.objects.create(organization=self.org, name='DS-01', status='active',
                                           permissions=ALL_ON)
        self.campaign = Campaign.objects.create(organization=self.org, name='H-04')
        self.login = User.objects.create(username='ds', email='ds@x.io', organization=self.org,
                                         role='publisher', publisher=self.ds)
        self.admin = User.objects.create(username='adm', email='adm@x.io', organization=self.org,
                                         role='admin')
        self.pub_api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.login)}')
        self.admin_api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')

    def calls(self, api):
        p = f'/api/publishers/{self.ds.id}'
        j = 'application/json'
        return {
            'pause': api.post(f'{p}/pause'),
            'activate': api.post(f'{p}/activate'),
            'caps': api.patch(f'{p}/cap', json.dumps({'max_calls_daily': 3}), content_type=j),
            'edit': api.patch(p, json.dumps({'name': 'DS-02'}), content_type=j),
            'link campaign': api.post(f'{p}/campaigns', json.dumps({'campaign_id': str(self.campaign.id)}),
                                      content_type=j),
            'unlink campaign': api.delete(f'{p}/campaigns/{self.campaign.id}'),
            'delete': api.delete(p),
        }

    def test_a_publisher_login_only_views_its_publisher(self):
        self.assertEqual(self.pub_api.get(f'/api/publishers/{self.ds.id}').status_code, 200)
        self.assertEqual(self.pub_api.get('/api/publishers/').status_code, 200)
        for name, r in self.calls(self.pub_api).items():
            self.assertEqual(r.status_code, 403, name)
        self.ds.refresh_from_db()
        self.assertEqual((self.ds.name, self.ds.status), ('DS-01', 'active'))

    def test_staff_unchanged(self):
        self.assertEqual(self.admin_api.post(f'/api/publishers/{self.ds.id}/pause').status_code, 200)
        self.assertEqual(self.admin_api.post(f'/api/publishers/{self.ds.id}/activate').status_code, 200)
        self.ds.refresh_from_db()
        self.assertEqual(self.ds.status, 'active')
