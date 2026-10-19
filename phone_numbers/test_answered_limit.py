"""A number is deleted once it has taken its answered calls (CH-144)."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from campaigns.models import Campaign
from notifications.defaults import ensure_default_rules
from notifications.models import NotificationRule
from phone_numbers.lifecycle import answered_calls, retire_if_single_use
from phone_numbers.models import Carrier, PhoneNumber
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
DIGITS = '18774893792'


@override_settings(CACHES=CACHE)
class AnsweredLimitTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Org')
        self.campaign = Campaign.objects.create(organization=self.org, name='C')
        self.kmq = Carrier.objects.create(
            organization=self.org, name='Freedom', code='KMQ',
            lifetime_hours=24, max_answered_calls=10)
        self.number = PhoneNumber.objects.create(
            organization=self.org, number='+' + DIGITS, carrier=self.kmq, vendor='other')
        self.n = 0

    def calls(self, count, status='completed', age_hours=1, called=None):
        for _ in range(count):
            self.n += 1
            row = CallLog.objects.create(
                organization=self.org, campaign=self.campaign,
                caller_number='+1555000%04d' % self.n, called_number=called or '+' + DIGITS,
                twilio_call_sid='sid-%s' % self.n, status=status)
            CallLog.objects.filter(pk=row.pk).update(
                created_at=timezone.now() - timedelta(hours=age_hours))

    def status(self):
        self.number.refresh_from_db()
        return self.number.status

    def test_nine_answered_calls_leave_it_live(self):
        self.calls(9)
        retire_if_single_use('+' + DIGITS, 'c')
        self.assertEqual(self.status(), 'active')

    def test_the_call_that_reaches_the_limit_deletes_it(self):
        self.calls(10)
        self.assertTrue(retire_if_single_use('+' + DIGITS, 'c'))
        self.assertEqual(self.status(), 'released')

    def test_unanswered_calls_do_not_count(self):
        self.calls(9)
        self.calls(5, status='no_answer')
        retire_if_single_use('+' + DIGITS, 'c')
        self.assertEqual(self.status(), 'active')

    def test_only_the_last_24_hours_count(self):
        self.calls(8)
        self.calls(5, age_hours=30)
        self.assertEqual(answered_calls(self.number), 8)
        retire_if_single_use('+' + DIGITS, 'c')
        self.assertEqual(self.status(), 'active')

    def test_another_numbers_calls_do_not_count(self):
        self.calls(9)
        self.calls(5, called='+18005550000')
        retire_if_single_use('+' + DIGITS, 'c')
        self.assertEqual(self.status(), 'active')

    def test_a_carrier_without_a_limit_is_left_alone(self):
        self.kmq.max_answered_calls = 0
        self.kmq.save()
        self.calls(30)
        retire_if_single_use('+' + DIGITS, 'c')
        self.assertEqual(self.status(), 'active')

    def test_the_nightly_sweep_uses_the_same_count(self):
        from datetime import datetime
        from unittest import mock
        from zoneinfo import ZoneInfo
        import tasks
        # Past the sweep's 1:30 AM Eastern gate, with every call dated from it.
        now = datetime(2026, 10, 20, 3, 0, tzinfo=ZoneInfo('America/New_York'))
        with mock.patch('django.utils.timezone.now', return_value=now):
            for age, count in ((1, 8), (30, 5)):
                for _ in range(count):
                    self.n += 1
                    row = CallLog.objects.create(
                        organization=self.org, campaign=self.campaign,
                        caller_number='+1555000%04d' % self.n, called_number='+' + DIGITS,
                        twilio_call_sid='sid-%s' % self.n, status='completed')
                    CallLog.objects.filter(pk=row.pk).update(
                        created_at=now - timedelta(hours=age))
            tasks.nightly_answered_sweep()
        self.assertEqual(self.status(), 'active')


@override_settings(CACHES=CACHE)
class TfnDeletedAlertTests(TestCase):
    def test_every_workspace_gets_a_rule_for_it(self):
        org = Organization.objects.create(name='Org')
        ensure_default_rules(org)
        self.assertTrue(NotificationRule.objects.filter(
            organization=org, event='number.deleted', is_active=True).exists())

    def test_retiring_a_number_sends_the_alert(self):
        from unittest import mock
        from phone_numbers.lifecycle import retire
        org = Organization.objects.create(name='Org')
        carrier = Carrier.objects.create(organization=org, name='K', code='K', single_use=True)
        number = PhoneNumber.objects.create(
            organization=org, number='+18005550001', carrier=carrier, vendor='other')
        with mock.patch('notifications.services.NotificationService.dispatch') as send:
            retire(number, 'used')
        send.assert_called_once()
        self.assertEqual(send.call_args[0][0], 'number.deleted')
        self.assertEqual(send.call_args[0][2]['message'], 'TFN Deleted +18005550001')
