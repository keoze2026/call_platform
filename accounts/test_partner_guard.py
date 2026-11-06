"""CH-615 (Sir, 6 Nov: "Everything private - main acc; buyers to buyers things
only; publisher to publisher things only. Nothing exposed any more"): a buyer
or publisher login's API answers carry only its own things.

Run:  python manage.py test accounts.test_partner_guard
"""
import io
import os
import contextlib
from unittest import mock

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from accounts.partner_guard import emptied, scrub
from analytics.models import CallRecord
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


class ScrubTests(SimpleTestCase):
    ROW = {'called_number': '+18888914875', 'campaign_name': 'C-07', 'publisher_name': 'PDSO1',
           'publisher_payout': '0.50', 'buyer_name': 'AAT', 'destination_number': '+18449591265',
           'revenue': '1.00', 'caller_number': '+15551112222', 'payout': None,
           'campaign': {'id': 'c1', 'name': 'C-07'}, 'buyer': 'AAT',
           'nested': [{'campaignName': 'C-07', 'destinationNumber': '+18449591265'}]}

    def test_a_buyer_loses_our_tfn_campaign_publisher(self):
        out = scrub(self.ROW, 'buyer')
        self.assertEqual((out['called_number'], out['campaign_name'], out['publisher_name']), ('', '', ''))
        self.assertIsNone(out['publisher_payout'])
        self.assertEqual(out['campaign'], {'id': 'c1', 'name': ''})
        self.assertEqual(out['nested'][0]['campaignName'], '')
        # its own things stay
        self.assertEqual((out['buyer_name'], out['destination_number'], out['revenue'], out['caller_number']),
                         ('AAT', '+18449591265', '1.00', '+15551112222'))

    def test_a_publisher_loses_the_buyer_its_number_and_our_money(self):
        out = scrub(self.ROW, 'publisher')
        self.assertEqual((out['buyer_name'], out['destination_number'], out['buyer']), ('', '', ''))
        self.assertIsNone(out['revenue'])
        self.assertEqual(out['nested'][0]['destinationNumber'], '')
        self.assertEqual((out['campaign_name'], out['publisher_name'], out['called_number']),
                         ('C-07', 'PDSO1', '+18888914875'))

    def test_an_emptied_page_keeps_its_shape(self):
        self.assertEqual(emptied({'items': [1, 2], 'total': 2, 'page': 1}), {'items': [], 'total': 0, 'page': 1})
        self.assertEqual(emptied([1, 2]), [])


@override_settings(CACHES=CACHE, ALLOWED_HOSTS=['*'])
class LiveAnswersTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='A', slug='interns')
        self.aat = Buyer.objects.create(organization=self.org, name='AAT')
        self.tp9 = Buyer.objects.create(organization=self.org, name='TP9')
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO1')
        self.camp = Campaign.objects.create(organization=self.org, name='C-07', status='active')
        PhoneNumber.objects.create(organization=self.org, number='+18888914875', status='active',
                                   campaign=self.camp, publisher=self.pub)
        Destination.objects.create(organization=self.org, buyer=self.aat, name='AAT 1', tfn='+18449591265', enabled=True)
        Destination.objects.create(organization=self.org, buyer=self.tp9, name='TP9 1', tfn='+18889458405', enabled=True)
        CallLog.objects.create(organization=self.org, campaign=self.camp, buyer=self.aat, publisher=self.pub,
                               caller_number='+15551112222', called_number='+18888914875',
                               destination_number='+18449591265', status='completed', twilio_call_sid='s1')
        CallRecord.objects.create(organization=self.org, caller_number='5551112222', called_number='+18888914875',
                                  campaign=self.camp, campaign_name='C-07', buyer_id=self.aat.id, buyer_name='AAT',
                                  publisher_id=self.pub.id, publisher_name='PDSO1', destination_number='+18449591265',
                                  status='completed', revenue=1, payout=0.5)
        self.admin = User.objects.create(username='adm', email='adm@x.io', organization=self.org, role='admin',
                                         is_active=True, is_superuser=True)
        self.buyer = User.objects.create(username='b', email='aat@x.io', organization=self.org, role='buyer',
                                         buyer=self.aat, is_active=True)
        self.publisher = User.objects.create(username='p', email='pub@x.io', organization=self.org,
                                             role='publisher', publisher=self.pub, is_active=True)

    def api(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def test_staff_answers_are_untouched(self):
        body = self.api(self.admin).get('/api/routing/calls').json()
        row = body['items'][0]
        self.assertEqual(row['called_number'], '+18888914875')
        self.assertTrue(self.api(self.admin).get('/api/accounts/workspace/members').json()['items'])

    def test_a_buyer_gets_no_campaigns_no_feed_no_members(self):
        api = self.api(self.buyer)
        for path in ('/api/campaigns/', '/api/accounts/workspace/activity', '/api/accounts/workspace/members',
                     '/api/routing/rules'):
            r = api.get(path)
            if r.status_code == 200:
                body = r.json()
                self.assertEqual(body if isinstance(body, list) else body.get('items', []), [], path)

    def test_the_exposure_check_finds_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            # CH-619: the script is in the repository (it was read from a path
            # that exists on one machine only, so the test failed elsewhere).
            script = os.path.join(settings.BASE_DIR, 'scripts', 'exposure_check.py')
            exec(open(script).read(), {'__name__': '__main__'})
        self.assertIn('TOTAL exposures: 0', out.getvalue(), out.getvalue()[-3000:])
