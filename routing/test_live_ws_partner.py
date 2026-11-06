"""CH-630: the Live Monitor connection (websocket) gives a buyer or publisher
login only what the API pages give it (CH-615) - and every login only its
own account's calls.

Run:  python manage.py test routing.test_live_ws_partner
"""
from django.test import TestCase

from accounts.models import Organization, User
from buyers.models import Buyer
from campaigns.models import Campaign
from publishers.models import Publisher
from routing.consumers import live_calls_for
from routing.models import CallLog


class LiveSnapshotTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.other = Organization.objects.create(name='Texora', slug='texora')
        self.camp = Campaign.objects.create(organization=self.org, name='H-02')
        self.bop = Buyer.objects.create(organization=self.org, name='BOP')
        self.pd = Publisher.objects.create(organization=self.org, name='PD-02')
        CallLog.objects.create(organization=self.org, campaign=self.camp, buyer=self.bop, publisher=self.pd,
                               caller_number='+15550001111', called_number='+18774894147',
                               destination_number='+18889022059', twilio_call_sid='CAWS1', status='in_progress')
        CallLog.objects.create(organization=self.other, caller_number='+15550002222',
                               called_number='+18005550100', twilio_call_sid='CAWS2', status='in_progress')
        self.admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin')
        self.buyer = User.objects.create(username='b', email='b@x.io', organization=self.org, role='buyer',
                                         buyer=self.bop)
        self.publisher = User.objects.create(username='p', email='p@x.io', organization=self.org,
                                             role='publisher', publisher=self.pd)

    def test_staff_see_their_account_in_full(self):
        rows = live_calls_for(self.admin)
        self.assertEqual(len(rows), 1)                                  # never Texora's call
        self.assertEqual((rows[0]['called_number'], rows[0]['campaign_name'], rows[0]['buyer_name']),
                         ('+18774894147', 'H-02', 'BOP'))

    def test_a_buyer_login_gets_no_tfn_or_campaign(self):
        rows = live_calls_for(self.buyer)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['called_number'], rows[0]['campaign_name']), ('', ''))
        self.assertEqual(rows[0]['buyer_name'], 'BOP')                  # its own name stays

    def test_a_publisher_login_gets_no_buyer(self):
        rows = live_calls_for(self.publisher)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['buyer_name'], rows[0]['destination_number']), ('', ''))
        self.assertEqual(rows[0]['called_number'], '+18774894147')      # its own TFN stays
