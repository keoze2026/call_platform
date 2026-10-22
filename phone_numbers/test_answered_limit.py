"""The threshold matrix (Sir, 20 Oct): a number is retired only when it took
MORE than its limit of answered calls on one Eastern calendar day, and only at
the nightly 1:30 AM Eastern sweep, never mid-day."""
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from campaigns.models import Campaign
from notifications.defaults import ensure_default_rules
from notifications.models import NotificationRule
from phone_numbers.lifecycle import answered_calls_on, retire_expired, retire_if_single_use
from phone_numbers.models import Carrier, PhoneNumber
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
DIGITS = '18774893792'
ET = ZoneInfo('America/New_York')
# Tuesday 3 AM Eastern: past the sweep's 1:30 gate. The day that just ended is Monday the 19th.
NIGHT = datetime(2026, 10, 20, 3, 0, tzinfo=ET)
MONDAY = date(2026, 10, 19)
SUNDAY = date(2026, 10, 18)


@override_settings(CACHES=CACHE)
class DailyThresholdTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.org = Organization.objects.create(name='Org')
        self.campaign = Campaign.objects.create(organization=self.org, name='C')
        self.kmq = Carrier.objects.create(
            organization=self.org, name='Freedom', code='KMQ',
            lifetime_hours=24, max_answered_calls=10)
        self.number = PhoneNumber.objects.create(
            organization=self.org, number='+' + DIGITS, carrier=self.kmq, vendor='other')
        self.n = 0

    def calls(self, count, when, status='completed', called=None):
        for _ in range(count):
            self.n += 1
            row = CallLog.objects.create(
                organization=self.org, campaign=self.campaign,
                caller_number='+1555000%04d' % self.n, called_number=called or '+' + DIGITS,
                twilio_call_sid='sid-%s' % self.n, status=status)
            CallLog.objects.filter(pk=row.pk).update(created_at=when)

    def at(self, day, hour=12, minute=0):
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)

    def status(self):
        self.number.refresh_from_db()
        return self.number.status

    def sweep(self, now=NIGHT):
        import tasks
        with mock.patch('django.utils.timezone.now', return_value=now):
            tasks.nightly_answered_sweep()

    # Nothing happens while the shift is running.
    def test_a_call_that_goes_over_the_limit_does_not_retire_it_mid_day(self):
        self.calls(15, self.at(MONDAY))
        self.assertFalse(retire_if_single_use('+' + DIGITS, 'c'))
        self.assertEqual(self.status(), 'active')

    def test_the_sweep_does_nothing_before_1_30_am_eastern(self):
        self.calls(15, self.at(MONDAY))
        self.sweep(datetime(2026, 10, 20, 1, 29, tzinfo=ET))
        self.assertEqual(self.status(), 'active')

    # The matrix itself.
    def test_more_than_the_limit_on_one_day_is_retired_by_the_sweep(self):
        self.calls(11, self.at(MONDAY))
        self.sweep()
        self.assertEqual(self.status(), 'released')

    def test_exactly_the_limit_stays_live(self):
        self.calls(10, self.at(MONDAY))
        self.sweep()
        self.assertEqual(self.status(), 'active')

    def test_days_are_not_combined(self):
        self.calls(6, self.at(SUNDAY))
        self.calls(6, self.at(MONDAY))
        self.sweep()
        self.assertEqual(self.status(), 'active')

    def test_a_busy_day_before_yesterday_does_not_count_tonight(self):
        self.calls(20, self.at(SUNDAY))
        self.calls(3, self.at(MONDAY))
        self.sweep()
        self.assertEqual(self.status(), 'active')

    def test_calls_made_after_midnight_belong_to_the_new_day(self):
        self.calls(8, self.at(MONDAY, 23, 30))
        self.calls(5, self.at(date(2026, 10, 20), 0, 30))
        self.assertEqual(answered_calls_on(self.number, MONDAY), 8)
        self.sweep()
        self.assertEqual(self.status(), 'active')

    def test_unanswered_calls_do_not_count(self):
        self.calls(9, self.at(MONDAY))
        self.calls(5, self.at(MONDAY), status='no_answer')
        self.sweep()
        self.assertEqual(self.status(), 'active')

    def test_another_numbers_calls_do_not_count(self):
        self.calls(9, self.at(MONDAY))
        self.calls(5, self.at(MONDAY), called='+18005550000')
        self.sweep()
        self.assertEqual(self.status(), 'active')

    def test_a_carrier_without_a_limit_is_left_alone(self):
        self.kmq.max_answered_calls = 0
        self.kmq.lifetime_hours = 0
        self.kmq.save()
        self.calls(30, self.at(MONDAY))
        self.sweep()
        self.assertEqual(self.status(), 'active')

    def test_the_sweep_runs_once_per_day(self):
        self.calls(11, self.at(MONDAY))
        self.sweep()
        self.assertEqual(self.status(), 'released')
        self.number.status = 'active'
        self.number.save()
        self.sweep(datetime(2026, 10, 20, 4, 0, tzinfo=ET))
        self.assertEqual(self.status(), 'active')

    def test_the_retirement_drops_the_campaign_and_publisher(self):
        self.number.campaign = self.campaign
        self.number.save()
        self.calls(11, self.at(MONDAY))
        self.sweep()
        self.number.refresh_from_db()
        self.assertIsNone(self.number.campaign)

    # The 24 hour clock does not apply while a daily threshold is set.
    def test_no_24_hour_clock_with_a_daily_threshold(self):
        retire_if_single_use('+' + DIGITS, 'c')
        self.number.refresh_from_db()
        self.assertIsNotNone(self.number.used_at)
        self.assertIsNone(self.number.expires_at)

    def test_a_number_past_an_old_deadline_is_not_expired_mid_day(self):
        PhoneNumber.objects.filter(pk=self.number.pk).update(
            expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(retire_expired(), 0)
        self.assertEqual(self.status(), 'active')

    # A carrier with only a fixed window keeps that window, but only the
    # nightly sweep applies it.
    def test_a_lifetime_only_carrier_is_retired_at_the_sweep_not_before(self):
        carrier = Carrier.objects.create(
            organization=self.org, name='H24', code='H24', lifetime_hours=24)
        other = PhoneNumber.objects.create(
            organization=self.org, number='+18005550123', carrier=carrier, vendor='other',
            used_at=NIGHT - timedelta(hours=30), expires_at=NIGHT - timedelta(hours=6))
        self.sweep(datetime(2026, 10, 20, 1, 0, tzinfo=ET))
        other.refresh_from_db()
        self.assertEqual(other.status, 'active')
        self.sweep(NIGHT)
        other.refresh_from_db()
        self.assertEqual(other.status, 'released')


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
        self.assertEqual(send.call_args[0][2]['message'],
                         '1 TFN Expired\n+1 800-555-0001\nPlease add new TFNs to continue routing calls.')
