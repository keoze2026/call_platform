"""CH-576: the workspace's AI insights and Autopilot stay inside the workspace.

Every buyer and publisher login was shown "7 buyers are missing calls",
"Revenue Decline" and the rest in the bell, and could run Autopilot.

Run:  python manage.py test analytics.test_ai_partner_access
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.ai_insights import AIInsightsService
from buyers.models import Buyer
from publishers.models import Publisher

INSIDE = [{'type': 'warning', 'category': 'buyer', 'title': 'Buyer TP9 has high no-answer rate',
           'message': 'x', 'action': 'pause_buyer', 'entity_id': '1'}]
INSIDE_ANOMALY = [{'type': 'warning', 'title': 'Revenue Decline', 'message': 'x', 'metric': 'revenue',
                   'value': 1.0, 'previous': 2.0, 'change_percent': -50.0}]


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AiPartnerAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        buyer = Buyer.objects.create(organization=self.org, name='TP9')
        pub = Publisher.objects.create(organization=self.org, name='PDSO 1')
        self.admin = self.login('admin@x.io', 'admin')
        self.partners = [self.login('b@x.io', 'buyer', buyer=buyer),
                         self.login('p@x.io', 'publisher', publisher=pub)]
        for name, value in (('get_recommendations', INSIDE), ('get_anomalies', INSIDE_ANOMALY)):
            p = patch.object(AIInsightsService, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def login(self, email, role, **kw):
        return User.objects.create_user(username=email, email=email, password='x', organization=self.org, role=role, **kw)

    def call(self, user, method, path, **kw):
        token = str(AccessToken.for_user(user))
        return getattr(Client(), method)(f'/api/ai/{path}', HTTP_AUTHORIZATION=f'Bearer {token}', **kw)

    def test_partner_gets_no_insights(self):
        for user in self.partners:
            for path in ('recommendations/', 'anomalies/'):
                r = self.call(user, 'get', path)
                self.assertEqual((user.role, path, r.status_code, r.json()), (user.role, path, 200, []))

    def test_main_account_keeps_its_insights(self):
        self.assertEqual(self.call(self.admin, 'get', 'recommendations/').json()[0]['title'], INSIDE[0]['title'])
        self.assertEqual(self.call(self.admin, 'get', 'anomalies/').json()[0]['title'], 'Revenue Decline')

    def test_partner_cannot_touch_autopilot(self):
        with patch('analytics.ai_api.autopilot.run_pass') as run, \
                patch('analytics.ai_api.autopilot.update_options') as update:
            for user in self.partners:
                self.assertEqual(self.call(user, 'post', 'autopilot/').status_code, 403)
                self.assertEqual(self.call(user, 'get', 'autopilot/config/').status_code, 403)
                self.assertEqual(self.call(user, 'patch', 'autopilot/config/', data='{"auto_run": true}',
                                           content_type='application/json').status_code, 403)
        run.assert_not_called()
        update.assert_not_called()

    def test_main_account_keeps_autopilot(self):
        self.assertEqual(self.call(self.admin, 'get', 'autopilot/config/').status_code, 200)
