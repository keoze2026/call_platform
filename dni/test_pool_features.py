"""CH-456 (Sir, 31 Oct): number pools - advertisers only, TFNs added from Phone
Numbers, cool-down, pool figures, the pool's own websites."""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.models import Buyer
from dni.attribution import mark_called
from dni.models import DNINumber, DNIPool, DNISession
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher
from routing.models import CallLog


@override_settings(ALLOWED_HOSTS=['*'], DNI_COOLDOWN_MINUTES=10,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CORS_ALLOWED_ORIGINS=['https://app.avortyx.io'])
class PoolFeatureTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Acme', slug='acme')
        admin = User.objects.create(username='ad', email='ad@x.io', organization=self.org, role='admin', is_active=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        self.tfn1 = PhoneNumber.objects.create(organization=self.org, number='+18885550101', vendor='Other')
        self.tfn2 = PhoneNumber.objects.create(organization=self.org, number='+18885550102', vendor='Other')
        self.seq = 0

    def login(self, role, **kw):
        u = User.objects.create(username=role, email=f'{role}@x.io', organization=self.org, role=role, is_active=True, **kw)
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')

    def pool(self, **kw):
        body = {'name': 'Site', 'fallback_number': '+18005550000', **kw}
        r = self.api.post('/api/dni/pools', body, content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def assign(self, pool_id, visitor, origin='https://example.com'):
        return Client().post('/api/dni/assign', {'pool_id': pool_id, 'visitor_id': visitor, 'utm_source': 'google'},
                             content_type='application/json', HTTP_ORIGIN=origin)

    def figures(self, pool_id):
        return self.api.get(f'/api/dni/pools/{pool_id}').json()

    def test_advertisers_only(self):
        pub = Publisher.objects.create(organization=self.org, name='P', permissions={'number_creation': True})
        buyer = Buyer.objects.create(organization=self.org, name='B')
        for c in (self.login('publisher', publisher=pub), self.login('buyer', buyer=buyer)):
            self.assertEqual(c.get('/api/dni/pools').status_code, 403)
            self.assertEqual(c.post('/api/dni/pools', {'name': 'x'}, content_type='application/json').status_code, 403)
        self.assertEqual(self.api.get('/api/dni/pools').status_code, 200)

    def test_tfns_are_added_and_removed_from_phone_numbers(self):
        p = self.pool(attached_number_ids=[str(self.tfn1.id)])
        self.assertEqual(p['attached_number_ids'], [str(self.tfn1.id)])
        r = self.api.post(f"/api/dni/pools/{p['id']}/numbers", {'number_id': str(self.tfn2.id)}, content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.figures(p['id'])['number_count'], 2)
        # the page saves the ticked list
        r = self.api.patch(f"/api/dni/pools/{p['id']}", {'attached_number_ids': [str(self.tfn2.id)]}, content_type='application/json')
        self.assertEqual(r.json()['attached_number_ids'], [str(self.tfn2.id)])
        r = self.api.delete(f"/api/dni/pools/{p['id']}/numbers/{self.tfn2.id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.figures(p['id'])['number_count'], 0)
        # one TFN, one pool
        other = self.pool(name='Other', attached_number_ids=[str(self.tfn1.id)])
        r = self.api.post(f"/api/dni/pools/{p['id']}/numbers", {'number_id': str(self.tfn1.id)}, content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Other', r.json()['detail'])

    def test_switch_and_idle_time_from_the_portal(self):
        p = self.pool(idle_time_sec=900)
        self.assertEqual(p['idle_time_sec'], 900)
        r = self.api.patch(f"/api/dni/pools/{p['id']}", {'active': False}, content_type='application/json').json()
        self.assertEqual((r['active'], r['status']), (False, 'paused'))

    def test_cool_down_then_reuse(self):
        p = self.pool(attached_number_ids=[str(self.tfn1.id)])
        first = self.assign(p['id'], 'v1').json()
        self.assertEqual(first['phone_number'], '+18885550101')
        call = CallLog.objects.create(organization=self.org, caller_number='+15555550123',
                                      called_number='18885550101', twilio_call_sid='s1')
        self.assertTrue(mark_called(call))
        f = self.figures(p['id'])
        self.assertEqual((f['numbers_in_use'], f['numbers_cooling'], f['numbers_free']), (0, 1, 0))
        # resting: the next visitor gets the fallback, and it is counted
        self.assertEqual(self.assign(p['id'], 'v2').json()['phone_number'], '+18005550000')
        f = self.figures(p['id'])
        self.assertEqual(f['fallback_today'], 1)
        self.assertTrue(f['too_small'])
        # after the cool-down the number is shown again
        DNISession.objects.update(updated_at=timezone.now() - timedelta(minutes=11))
        self.assertEqual(self.assign(p['id'], 'v3').json()['phone_number'], '+18885550101')

    def test_numbers_rotate_and_peak_is_counted(self):
        p = self.pool(attached_number_ids=[str(self.tfn1.id), str(self.tfn2.id)])
        a = self.assign(p['id'], 'v1').json()['phone_number']
        b = self.assign(p['id'], 'v2').json()['phone_number']
        self.assertEqual({a, b}, {'+18885550101', '+18885550102'})
        f = self.figures(p['id'])
        self.assertEqual((f['numbers_in_use'], f['peak_visitors_7d']), (2, 2))
        self.assertTrue(f['too_small'])

    def test_calls_today_on_pool_numbers(self):
        p = self.pool(attached_number_ids=[str(self.tfn1.id)])
        CallLog.objects.create(organization=self.org, caller_number='+15555550123',
                               called_number='18885550101', twilio_call_sid='c1')
        CallLog.objects.create(organization=self.org, caller_number='+15555550124',
                               called_number='18885559999', twilio_call_sid='c2')
        self.assertEqual(self.figures(p['id'])['calls_today'], 1)

    def test_only_the_pools_websites_and_cors_for_the_script(self):
        p = self.pool(attached_number_ids=[str(self.tfn1.id)], allowed_domains=['example.com'])
        self.assertEqual(self.assign(p['id'], 'v1', origin='https://evil.com').status_code, 403)
        self.assertEqual(self.assign(p['id'], 'v2', origin='https://shop.example.com').status_code, 200)
        pre = Client().options('/api/dni/assign', HTTP_ORIGIN='https://shop.example.com',
                               HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
                               HTTP_ACCESS_CONTROL_REQUEST_HEADERS='content-type')
        self.assertEqual(pre.get('Access-Control-Allow-Origin'), 'https://shop.example.com')
        other = Client().options('/api/dni/pools', HTTP_ORIGIN='https://shop.example.com',
                                 HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET')
        self.assertIsNone(other.get('Access-Control-Allow-Origin'))

    def test_snippet_posts_back_to_this_server(self):
        p = self.pool()
        js = Client().get(f"/api/dni/snippet/{p['id']}", HTTP_HOST='api.avortyx.io').content.decode()
        self.assertIn("'http://api.avortyx.io/api/dni/assign'", js)
        self.assertNotIn('yourdomain', js)
