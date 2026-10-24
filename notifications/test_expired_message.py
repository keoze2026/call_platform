"""CH-199: the TFNs-expired alert is Sir's short message, one per batch, with no
carrier codes or rules in it.

Run:  python manage.py test notifications.test_expired_message
"""
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from accounts.models import Organization
from campaigns.models import Campaign
from notifications.services import NotificationService
from phone_numbers.lifecycle import expired_message, sweep_over_limit
from phone_numbers.models import Carrier, PhoneNumber
from routing.models import CallLog

ET = ZoneInfo('America/New_York')
SHORT = ('2 TFNs Expired\nThe following numbers have expired and are no longer receiving calls:\n'
         '18774893789\n18774893790\nAdd replacement TFNs to keep calls routing without interruption.')


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ExpiredMessageTests(TestCase):
    def test_the_wording(self):
        data = expired_message(['+18774893789', '+18774893790'])
        self.assertEqual(data['title'], '2 TFNs Expired')
        self.assertEqual(data['message'], SHORT)
        self.assertEqual(NotificationService._build_subject('number.deleted', data), '2 TFNs Expired')
        self.assertEqual(NotificationService._build_body('number.deleted', data), SHORT)

    def test_one_alert_for_the_whole_nightly_batch_and_no_backend_details(self):
        org = Organization.objects.create(name='Org')
        camp = Campaign.objects.create(organization=org, name='PDSO')
        kmq = Carrier.objects.create(organization=org, name='Freedom', code='KMQ', max_answered_calls=10)
        k = 0
        for digits in ('18774893789', '18774893790'):
            n = PhoneNumber.objects.create(organization=org, number='+' + digits, carrier=kmq,
                                           vendor='other', campaign=camp, status='paused')
            for _ in range(11):
                k += 1
                row = CallLog.objects.create(organization=org, campaign=camp, caller_number='+1555000%04d' % k,
                                             called_number=n.number, twilio_call_sid='s%d' % k, status='completed')
                CallLog.objects.filter(pk=row.pk).update(created_at=datetime(2026, 10, 21, 12, tzinfo=ET))
        with mock.patch('notifications.services.NotificationService.dispatch') as send:
            sweep_over_limit(date(2026, 10, 21))
        send.assert_called_once()
        data = send.call_args[0][2]
        self.assertEqual(data['message'], SHORT)
        text = str(data)
        for secret in ('KMQ', 'Freedom', 'limit', '1:30', 'answered'):
            self.assertNotIn(secret, text)
