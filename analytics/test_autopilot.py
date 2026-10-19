"""Every Autopilot option does what its switch says.

Each rule is tested both ways: switched off it does nothing, switched on it
acts, and its threshold moves the line.
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics import autopilot
from billing.models import BillingAccount
from buyers.models import Buyer
from campaigns.models import Campaign
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
ET = ZoneInfo('America/New_York')
# A Tuesday afternoon, well inside business hours and past the 3 hour mark.
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=ET)

_sid = 0


def make_call(org, when, buyer=None, status='completed', duration=60, revenue='1.00', **extra):
    global _sid
    _sid += 1
    log = CallLog.objects.create(
        organization=org, buyer=buyer, caller_number='+15550000001',
        called_number='+18885550000', twilio_call_sid=f'CA{_sid:08d}',
        status=status, duration=duration, revenue=Decimal(revenue), **extra,
    )
    CallLog.objects.filter(pk=log.pk).update(created_at=when)
    return log


@override_settings(CACHES=CACHE)
class AutopilotBase(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='AP', slug='ap')
        self.sent = []
        patcher = mock.patch(
            'notifications.services.NotificationService.dispatch',
            side_effect=lambda event, org, data: self.sent.append((event, data)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        clock = mock.patch('analytics.autopilot.timezone.now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        # Only the rule under test is on.
        self.set(**{k: False for k, v in autopilot.default_options().items() if v is True})

    def set(self, **options):
        autopilot.update_options(self.org, options)

    def run_pass(self):
        return autopilot.run_pass(self.org)

    def kinds(self, actions):
        return [a['action'] for a in actions]

    def buyer(self, name='B1'):
        return Buyer.objects.create(organization=self.org, name=name)

    def calls(self, buyer, n, **kw):
        for i in range(n):
            make_call(self.org, NOW - timedelta(hours=1, minutes=i), buyer=buyer, **kw)


class SettingsTests(AutopilotBase):
    def test_unknown_and_out_of_range_values_are_handled(self):
        out = autopilot.clean({'no_answer_threshold': 5000, 'bogus': 1, 'auto_run': 1,
                               'no_calls_hours': 'x'})
        self.assertEqual(out, {'no_answer_threshold': 100, 'auto_run': True})

    def test_saving_one_option_keeps_the_others(self):
        self.set(pause_on_high_no_answer=True, no_answer_threshold=55)
        self.set(alert_on_low_balance=True)
        opts = autopilot.options_for(autopilot.get_settings(self.org))
        self.assertTrue(opts['pause_on_high_no_answer'])
        self.assertEqual(opts['no_answer_threshold'], 55)
        self.assertTrue(opts['alert_on_low_balance'])

    def test_every_option_has_a_default_and_a_rule(self):
        # An option nothing reads is the bug this module exists to remove.
        import inspect
        source = inspect.getsource(autopilot)
        body = source.split('# ── the rules')[1]
        for key in autopilot.DEFAULTS:
            self.assertIn(f"'{key}'", body, f'{key} is never read by a rule')


class ApiTests(AutopilotBase):
    def setUp(self):
        super().setUp()
        user = User.objects.create(username='a', email='a@ap.io', organization=self.org,
                                   role='admin', is_active=True)
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_patch_changes_only_what_was_sent(self):
        self.set(pause_on_high_no_answer=True, alert_on_low_balance=True)
        r = self.client.patch('/api/ai/autopilot/config/', data=json.dumps({'auto_run': True}),
                              content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body['auto_run'])
        self.assertTrue(body['pause_on_high_no_answer'])
        self.assertTrue(body['alert_on_low_balance'])
        self.assertIn('history', body)

    def test_bad_number_is_refused(self):
        r = self.client.patch('/api/ai/autopilot/config/',
                              data=json.dumps({'no_answer_threshold': 900}),
                              content_type='application/json')
        self.assertEqual(r.status_code, 422)

    def test_run_now_uses_the_saved_switches(self):
        b = self.buyer()
        self.calls(b, 12, status='no_answer')
        r = self.client.post('/api/ai/autopilot/')
        self.assertEqual(r.json(), [])           # switch is off: nothing happens
        self.client.patch('/api/ai/autopilot/config/',
                          data=json.dumps({'pause_on_high_no_answer': True}),
                          content_type='application/json')
        r = self.client.post('/api/ai/autopilot/')
        self.assertEqual(self.kinds(r.json()), ['paused_buyer'])
        got = self.client.get('/api/ai/autopilot/config/').json()
        self.assertEqual(got['history'][0]['action'], 'paused_buyer')
        self.assertIsNotNone(got['last_run_at'])


class BuyerRules(AutopilotBase):
    def test_no_answer_pause_respects_switch_threshold_and_minimum(self):
        b = self.buyer()
        self.calls(b, 8, status='no_answer')
        self.calls(b, 4, status='completed')           # 8 of 12 = 67%
        self.assertEqual(self.run_pass(), [])          # switch off
        self.set(pause_on_high_no_answer=True)
        self.assertEqual(self.run_pass(), [])          # 67% is under 70%
        self.set(no_answer_threshold=60)
        self.assertEqual(self.kinds(self.run_pass()), ['paused_buyer'])
        b.refresh_from_db()
        self.assertEqual(b.status, 'paused')

    def test_too_few_calls_are_not_judged(self):
        b = self.buyer()
        self.calls(b, 5, status='no_answer')
        self.set(pause_on_high_no_answer=True)
        self.assertEqual(self.run_pass(), [])
        self.set(min_calls_to_judge=5)
        self.assertEqual(self.kinds(self.run_pass()), ['paused_buyer'])

    def test_short_calls_pause(self):
        b = self.buyer()
        self.calls(b, 12, status='completed', duration=6)
        self.assertEqual(self.run_pass(), [])
        self.set(pause_on_short_calls=True, short_call_seconds=15)
        actions = self.run_pass()
        self.assertEqual(self.kinds(actions), ['paused_buyer'])
        self.assertIn('Average call length', actions[0]['reason'])

    def test_healthy_buyer_is_left_alone(self):
        b = self.buyer()
        self.calls(b, 12, status='completed', duration=120)
        self.set(pause_on_high_no_answer=True, pause_on_short_calls=True)
        self.assertEqual(self.run_pass(), [])

    def test_resume_only_what_autopilot_paused_and_not_before_the_wait(self):
        mine, hand = self.buyer('mine'), self.buyer('hand')
        self.calls(mine, 12, status='no_answer')
        Buyer.objects.filter(pk=hand.pk).update(status='paused')
        self.set(pause_on_high_no_answer=True, resume_recovered_buyers=True, resume_after_minutes=60)
        self.run_pass()
        self.assertEqual(Buyer.objects.get(pk=mine.pk).status, 'paused')

        later = NOW + timedelta(minutes=30)
        with mock.patch('analytics.autopilot.timezone.now', return_value=later):
            self.assertEqual(autopilot.run_pass(self.org), [])
        self.assertEqual(Buyer.objects.get(pk=mine.pk).status, 'paused')

        later = NOW + timedelta(minutes=61)
        with mock.patch('analytics.autopilot.timezone.now', return_value=later):
            actions = autopilot.run_pass(self.org)
        self.assertEqual(self.kinds(actions), ['resumed_buyer'])
        self.assertEqual(Buyer.objects.get(pk=mine.pk).status, 'active')
        self.assertEqual(Buyer.objects.get(pk=hand.pk).status, 'paused')   # not ours

        # The calls that got it paused do not pause it again the moment it is back.
        with mock.patch('analytics.autopilot.timezone.now', return_value=later + timedelta(minutes=5)):
            self.assertEqual(autopilot.run_pass(self.org), [])
        self.assertEqual(Buyer.objects.get(pk=mine.pk).status, 'active')

    def test_resume_switch_off_keeps_it_paused(self):
        b = self.buyer()
        self.calls(b, 12, status='no_answer')
        self.set(pause_on_high_no_answer=True)
        self.run_pass()
        with mock.patch('analytics.autopilot.timezone.now', return_value=NOW + timedelta(days=2)):
            autopilot.run_pass(self.org)
        self.assertEqual(Buyer.objects.get(pk=b.pk).status, 'paused')


class AlertRules(AutopilotBase):
    def history(self, per_day, revenue='1.00'):
        """`per_day` calls before this time of day on each of the last 7 days."""
        for d in range(1, 8):
            for i in range(per_day):
                make_call(self.org, NOW - timedelta(days=d, hours=1, minutes=i), revenue=revenue)

    def test_volume_drop(self):
        self.history(20)
        for i in range(5):
            make_call(self.org, NOW - timedelta(hours=1, minutes=i))
        self.assertEqual(self.run_pass(), [])                  # off
        self.set(alert_on_volume_drop=True, volume_drop_threshold=50)
        actions = self.run_pass()
        self.assertEqual(self.kinds(actions), ['alert_sent'])
        self.assertIn('down 75%', actions[0]['entity'])
        self.assertEqual(self.sent[0][0], 'autopilot.alert')

    def test_volume_within_threshold_is_quiet(self):
        self.history(20)
        for i in range(15):
            make_call(self.org, NOW - timedelta(hours=1, minutes=i))
        self.set(alert_on_volume_drop=True, volume_drop_threshold=50)
        self.assertEqual(self.run_pass(), [])

    def test_revenue_drop(self):
        self.history(20, revenue='2.00')
        for i in range(20):
            make_call(self.org, NOW - timedelta(hours=1, minutes=i), revenue='0.50')
        self.set(alert_on_revenue_drop=True, revenue_drop_threshold=40)
        self.assertEqual(self.kinds(self.run_pass()), ['alert_sent'])

    def test_alert_is_not_repeated_but_still_shown(self):
        self.history(20)
        self.set(alert_on_volume_drop=True)
        self.assertEqual(self.kinds(self.run_pass()), ['alert_sent'])
        self.assertEqual(self.kinds(self.run_pass()), ['still_active'])
        self.assertEqual(len(self.sent), 1)
        # Only real actions are written to the history.
        self.assertEqual(len(autopilot.get_settings(self.org).history), 1)

    def test_low_balance(self):
        BillingAccount.objects.create(organization=self.org, balance=Decimal('12.00'))
        self.assertEqual(self.run_pass(), [])
        self.set(alert_on_low_balance=True, low_balance_amount=25)
        self.assertEqual(self.kinds(self.run_pass()), ['alert_sent'])
        cache.clear()
        self.set(low_balance_amount=10)
        self.assertEqual(self.run_pass(), [])

    def test_no_calls(self):
        make_call(self.org, NOW - timedelta(hours=6))
        self.assertEqual(self.run_pass(), [])
        self.set(alert_on_no_calls=True, no_calls_hours=2)
        self.assertEqual(self.kinds(self.run_pass()), ['alert_sent'])

    def test_no_calls_ignored_outside_business_hours(self):
        night = datetime(2026, 10, 20, 23, 0, tzinfo=ET)
        make_call(self.org, night - timedelta(hours=8))
        self.set(alert_on_no_calls=True)
        with mock.patch('analytics.autopilot.timezone.now', return_value=night):
            self.assertEqual(autopilot.run_pass(self.org), [])

    def test_high_spam(self):
        for i in range(10):
            make_call(self.org, NOW - timedelta(hours=1, minutes=i),
                      status='failed' if i < 5 else 'completed', ipqs_checked=i < 5)
        self.set(alert_on_high_spam=True, spam_rate_threshold=30)
        self.assertEqual(self.kinds(self.run_pass()), ['alert_sent'])
        cache.clear()
        self.set(spam_rate_threshold=60)
        self.assertEqual(self.run_pass(), [])

    def test_campaign_without_buyers(self):
        Campaign.objects.create(organization=self.org, name='Lonely')
        self.assertEqual(self.run_pass(), [])
        self.set(alert_on_campaign_without_buyers=True)
        actions = self.run_pass()
        self.assertEqual(self.kinds(actions), ['alert_sent'])
        self.assertIn('Lonely', actions[0]['entity'])


class Schedule(AutopilotBase):
    def test_runs_only_for_workspaces_that_asked_and_waits_its_interval(self):
        self.assertEqual(autopilot.run_due_passes(), 0)       # auto_run is off
        self.set(auto_run=True, run_every_minutes=15)
        self.assertEqual(autopilot.run_due_passes(), 1)
        self.assertEqual(autopilot.run_due_passes(), 0)       # just ran
        with mock.patch('analytics.autopilot.timezone.now', return_value=NOW + timedelta(minutes=16)):
            self.assertEqual(autopilot.run_due_passes(), 1)
        self.assertEqual(autopilot.get_settings(self.org).last_run_at, NOW + timedelta(minutes=16))

    def test_a_rule_that_fails_does_not_stop_the_others(self):
        Campaign.objects.create(organization=self.org, name='Lonely')
        self.set(alert_on_campaign_without_buyers=True, alert_on_low_balance=True)
        with mock.patch('analytics.autopilot._balance', side_effect=RuntimeError('boom')):
            with self.assertLogs('analytics.autopilot', level='ERROR'):
                actions = self.run_pass()
        self.assertEqual(self.kinds(actions), ['alert_sent'])
