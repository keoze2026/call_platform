"""CH-659: Qualified is the same number on every Reports tab.

DS, 9 Nov: H-04 read Qualified 57 on the Campaign tab and 62 on Dialed. The
Dialed tab (and every drill-down) counts each call's own verdict,
`is_qualified`: answered, and the caller had no ANSWERED call to the campaign
inside its window. The Campaign, Publisher and Caller Profile tabs counted the
older rule, "answered and not Repeated" - and since CH-230 Repeated counts ANY
earlier call, a caller missed first and answered on the next call was left out
there.

Run:  python manage.py test analytics.test_qualified_same_everywhere
"""
from django.test import TestCase
from django.utils import timezone

from accounts.models import Organization, User
from analytics.models import CallRecord
from analytics.schemas import AnalyticsFilterSchema
from analytics.services import AnalyticsService as A
from campaigns.models import Campaign
from publishers.models import Publisher


class QualifiedSameEverywhereTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.user = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin')
        self.pub = Publisher.objects.create(organization=self.org, name='DS')
        self.camp = Campaign.objects.create(organization=self.org, name='H-04')
        now = timezone.now()
        self.n = 0
        # (caller, status, Repeated, Qualified) as routing/signals.py writes them.
        for caller, status, dup, qual in [
            ('+15550000001', 'completed', False, True),    # new caller, answered
            ('+15550000002', 'no_answer', False, False),   # missed first ...
            ('+15550000002', 'completed', True, True),     # ... answered next: Qualified, and Repeated
            ('+15550000003', 'completed', False, True),    # answered ...
            ('+15550000003', 'completed', True, False),    # ... answered again: Repeated, not Qualified
        ]:
            self.n += 1
            CallRecord.objects.create(
                organization=self.org, caller_number=caller, called_number='+18774894122',
                status=status, started_at=now, campaign=self.camp, campaign_name='H-04',
                publisher_id=self.pub.id, publisher_name='DS', carrier='Verizon',
                is_duplicate=dup, is_qualified=qual, twilio_call_sid=f's{self.n}')
        d = timezone.localtime(now).date().isoformat()
        self.f = AnalyticsFilterSchema(date_from=d, date_to=d, timezone='UTC')

    def test_every_tab_counts_the_calls_own_verdict(self):
        per_call = CallRecord.objects.filter(is_qualified=True).count()     # what Dialed adds up
        self.assertEqual(per_call, 3)
        for name, rows in (('campaign', A.get_campaign_performance(self.user, self.f)),
                           ('publisher', A.get_publisher_performance(self.user, self.f)),
                           ('carrier', A.get_carrier_performance(self.user, self.f))):
            self.assertEqual(sum(r['qualified_calls'] for r in rows), per_call, name)
