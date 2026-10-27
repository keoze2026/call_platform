"""CH-335: buyer and publisher logins get the pop-up alerts of their own access.

Run:  python manage.py test notifications.test_partner_popups
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PartnerPopupTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='avx')

    def login(self, role, **extra):
        u = User.objects.create(username=role, email=f'{role}@x.io', organization=self.org,
                                role=role, is_active=True, **extra)
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')

    def events(self, api):
        r = api.get('/api/notifications/events')
        self.assertEqual(r.status_code, 200, r.content)
        return [e['event'] for e in r.json()]

    def test_main_account_sees_every_alert(self):
        self.assertEqual(len(self.events(self.login('admin'))), 15)

    def test_buyer_sees_only_its_own_alerts(self):
        api = self.login('buyer')
        ev = self.events(api)
        self.assertEqual(ev, ['call.completed', 'buyer.cap_reached', 'daily.summary', 'call.started',
                              'destination.cap_reached', 'buyer.missed'])
        prefs = api.get('/api/notifications/preferences').json()
        self.assertEqual(prefs['popup_events'], ['buyer.cap_reached', 'destination.cap_reached'])

    def test_publisher_sees_only_its_own_alerts(self):
        ev = self.events(self.login('publisher'))
        for main_only in ('campaign.cap_reached', 'buyer.cap_reached', 'low.balance', 'campaign.paused',
                          'aht.low', 'autopilot.alert', 'destination.cap_reached'):
            self.assertNotIn(main_only, ev)
        self.assertIn('publisher.cap_reached', ev)
        self.assertIn('number.not_working', ev)

    def test_partner_can_switch_its_own_and_never_the_main_accounts(self):
        api = self.login('buyer')
        r = api.patch('/api/notifications/preferences',
                      data=json.dumps({'popup_events': ['call.started', 'low.balance', 'campaign.cap_reached']}),
                      content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['popup_events'], ['call.started'])

    def test_a_superuser_is_never_a_partner(self):
        self.assertEqual(len(self.events(self.login('buyer', is_superuser=True))), 15)
