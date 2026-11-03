"""CH-498: a publisher (or campaign, buyer) renamed during the range is one
row under its current name, with all its calls.

Seen 3 Nov: OLDC11 (once called "Test") came back as two rows with one id -
OLDC11 92 and Test 1 - and Reports showed OLDC11 with 1 call.

Run:  python manage.py test analytics.test_renamed_entity_one_row
"""
from django.test import TestCase
from django.utils import timezone

from accounts.models import Organization, User
from analytics.models import CallRecord
from analytics.schemas import AnalyticsFilterSchema
from analytics.services import AnalyticsService as A
from buyers.models import Buyer
from campaigns.models import Campaign
from publishers.models import Publisher
from routing.models import CallLog


class RenamedEntityOneRowTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.user = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin')
        self.pub = Publisher.objects.create(organization=self.org, name='OLDC11')
        self.buyer = Buyer.objects.create(organization=self.org, name='CRM')
        self.camp = Campaign.objects.create(organization=self.org, name='Nov 3')
        now = timezone.now()
        for i, (pname, bname, cname) in enumerate([('Test', 'Old CRM', 'Old Nov')] + [('OLDC11', 'CRM', 'Nov 3')] * 5):
            CallRecord.objects.create(
                organization=self.org, caller_number=f'+1555000{i:04d}', called_number='+18668215575',
                status='completed', started_at=now, publisher_id=self.pub.id, publisher_name=pname,
                buyer_id=self.buyer.id, buyer_name=bname, campaign=self.camp, campaign_name=cname)
        # Two calls live now: the row keeps them (they went to whichever of
        # the two rows came first, and the Reports page showed the other).
        for i in range(2):
            CallLog.objects.create(organization=self.org, campaign=self.camp, publisher=self.pub,
                                   buyer=self.buyer, caller_number=f'+1555111000{i}',
                                   called_number='+18668215575', twilio_call_sid=f'live-{i}',
                                   status='in_progress')
        d = timezone.localtime(now).date().isoformat()
        self.f = AnalyticsFilterSchema(date_from=d, date_to=d, timezone='UTC')

    def test_publisher(self):
        rows = A.get_publisher_performance(self.user, self.f)
        self.assertEqual([(r['publisher_name'], r['total_calls']) for r in rows], [('OLDC11', 8)])
        self.assertEqual(rows[0]['live_calls'], 2)
        self.assertEqual(rows[0]['connected_calls'], 8)  # 6 completed + 2 live
        self.assertEqual(rows[0]['not_connected_calls'], 0)

    def test_buyer(self):
        rows = A.get_buyer_performance(self.user, self.f)
        self.assertEqual([(r['buyer_name'], r['total_calls']) for r in rows], [('CRM', 8)])

    def test_campaign(self):
        rows = A.get_campaign_performance(self.user, self.f)
        self.assertEqual([(r['campaign_name'], r['total_calls']) for r in rows], [('Nov 3', 8)])
