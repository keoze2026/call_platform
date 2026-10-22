"""CH-207/CH-209: more calls than yesterday is good news, not a Critical anomaly,
worded formally ("Elevated Call Activity").

Run:  python manage.py test analytics.test_volume_spike
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from analytics.ai_insights import AIInsightsService
from campaigns.models import Campaign
from routing.models import CallLog


@override_settings(TIME_ZONE='UTC')
class VolumeSpikeTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Org')
        self.campaign = Campaign.objects.create(organization=self.org, name='C')
        self.n = 0

    def calls(self, count, days_ago):
        for _ in range(count):
            self.n += 1
            row = CallLog.objects.create(
                organization=self.org, campaign=self.campaign, caller_number='+15551234567',
                called_number='+15559990000', twilio_call_sid=f'sid-{self.n}')
            CallLog.objects.filter(pk=row.pk).update(
                created_at=timezone.now() - timedelta(days=days_ago))

    def volume(self):
        return [a for a in AIInsightsService.get_anomalies(self.org) if a['metric'] == 'call_volume']

    def test_a_spike_reads_as_good_news(self):
        self.calls(2, days_ago=1)
        self.calls(5, days_ago=0)
        [a] = self.volume()
        self.assertEqual(a['type'], 'success')
        self.assertEqual(a['title'], 'Elevated Call Activity')
        self.assertEqual(a['message'], 'Call volume is 150% higher than yesterday.')

    def test_a_drop_is_still_critical(self):
        self.calls(10, days_ago=1)
        self.calls(2, days_ago=0)
        [a] = self.volume()
        self.assertEqual(a['type'], 'critical')
        self.assertEqual(a['title'], 'Reduced Call Activity')
        self.assertEqual(a['message'], 'Call volume is 80% lower than yesterday.')
