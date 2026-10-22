"""CH-197: a paused TFN that went over its daily limit is retired at 1:30 AM too.

Run:  python manage.py test phone_numbers.test_paused_sweep
"""
from datetime import date, datetime
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import Organization
from campaigns.models import Campaign
from phone_numbers.models import Carrier, PhoneNumber
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
ET = ZoneInfo('America/New_York')
NIGHT = datetime(2026, 10, 22, 3, 0, tzinfo=ET)
DAY = date(2026, 10, 21)


@override_settings(CACHES=CACHE)
class PausedSweepTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.org = Organization.objects.create(name='Org')
        self.campaign = Campaign.objects.create(organization=self.org, name='PDSO')
        self.kmq = Carrier.objects.create(organization=self.org, name='Freedom', code='KMQ',
                                          lifetime_hours=24, max_answered_calls=10)
        self.n = 0

    def number(self, digits, status='active'):
        return PhoneNumber.objects.create(organization=self.org, number='+' + digits, carrier=self.kmq,
                                          vendor='other', campaign=self.campaign, status=status)

    def calls(self, number, count):
        for _ in range(count):
            self.n += 1
            row = CallLog.objects.create(organization=self.org, campaign=self.campaign,
                                         caller_number='+1555000%04d' % self.n, called_number=number.number,
                                         twilio_call_sid='sid-%s' % self.n, status='completed')
            CallLog.objects.filter(pk=row.pk).update(created_at=datetime(2026, 10, 21, 12, tzinfo=ET))

    def sweep(self):
        import tasks
        with mock.patch('django.utils.timezone.now', return_value=NIGHT), \
                mock.patch('notifications.services.NotificationService.dispatch') as send:
            tasks.nightly_answered_sweep()
        return send

    def test_a_paused_number_over_the_limit_is_retired_with_the_message(self):
        n = self.number('18885550001', status='paused')
        self.calls(n, 11)
        send = self.sweep()
        n.refresh_from_db()
        self.assertEqual((n.status, n.campaign_id), ('released', None))
        self.assertEqual(send.call_args[0][0], 'number.deleted')

    def test_a_paused_number_inside_the_limit_stays(self):
        n = self.number('18885550002', status='paused')
        self.calls(n, 10)
        self.sweep()
        n.refresh_from_db()
        self.assertEqual(n.status, 'paused')

    def test_the_command_lists_then_retires(self):
        n = self.number('18885550003', status='paused')
        self.calls(n, 12)
        out = StringIO()
        call_command('retire_over_limit', '--day', '2026-10-21', stdout=out)
        self.assertIn('1 numbers over their limit', out.getvalue())
        n.refresh_from_db()
        self.assertEqual(n.status, 'paused')
        with mock.patch('notifications.services.NotificationService.dispatch'):
            call_command('retire_over_limit', '--day', '2026-10-21', '--apply', stdout=StringIO())
        n.refresh_from_db()
        self.assertEqual(n.status, 'released')
