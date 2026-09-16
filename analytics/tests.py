from decimal import Decimal
from django.test import TestCase
from accounts.models import Organization, User
from campaigns.models import Campaign
from routing.models import CallLog
from .services import AnalyticsService


class CallerProfileTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Test Org')
        self.campaign = Campaign.objects.create(
            organization=self.org,
            name='Test Campaign',
        )

    def test_returns_empty_profile_for_unknown_caller(self):
        profile = AnalyticsService.get_profile(
            organization_id=str(self.org.id),
            caller_number='+15550000000',
        )
        self.assertEqual(profile['total_calls'], 0)
        self.assertFalse(profile['profile_exists'])

    def test_returns_aggregated_data_for_known_caller(self):
        for i in range(3):
            CallLog.objects.create(
                organization=self.org,
                campaign=self.campaign,
                caller_number='+15551234567',
                called_number='+15559999999',
                twilio_call_sid=f'CA_profile_{i}',
                status=CallLog.Status.COMPLETED,
                duration=120,
                revenue=Decimal('10.00'),
            )

        profile = AnalyticsService.get_profile(
            organization_id=str(self.org.id),
            caller_number='+15551234567',
        )

        self.assertTrue(profile['profile_exists'])
        self.assertEqual(profile['total_calls'], 3)
        self.assertEqual(profile['completed_calls'], 3)
        self.assertEqual(profile['completion_rate'], 100.0)
        self.assertEqual(profile['total_duration_seconds'], 360)
        self.assertEqual(profile['unique_campaigns'], 1)

    def test_completion_rate_calculation(self):
        # 2 completed, 1 failed
        for i in range(2):
            CallLog.objects.create(
                organization=self.org,
                campaign=self.campaign,
                caller_number='+15552223333',
                called_number='+15559999999',
                twilio_call_sid=f'CA_rate_{i}',
                status=CallLog.Status.COMPLETED,
                duration=60,
            )
        CallLog.objects.create(
            organization=self.org,
            campaign=self.campaign,
            caller_number='+15552223333',
            called_number='+15559999999',
            twilio_call_sid='CA_rate_fail',
            status=CallLog.Status.FAILED,
            duration=0,
        )

        profile = AnalyticsService.get_profile(
            organization_id=str(self.org.id),
            caller_number='+15552223333',
        )

        self.assertEqual(profile['total_calls'], 3)
        self.assertEqual(profile['completed_calls'], 2)
        self.assertAlmostEqual(profile['completion_rate'], 66.67, places=1)