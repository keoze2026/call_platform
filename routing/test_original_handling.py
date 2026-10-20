"""Original duplicate handling: a repeat caller always goes to the destination
that took their first call, however long ago that was (Sir, 20 Oct)."""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from campaigns.models import Campaign
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CALLER = '+15551230000'
FIRST = '+15550000001'
SECOND = '+15550000002'


@override_settings(CACHES=CACHE)
class OriginalHandlingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Org')
        self.campaign = Campaign.objects.create(
            organization=self.org, name='C', duplicate_handling='original')
        rule = RoutingRule.objects.create(
            organization=self.org, campaign=self.campaign, name='R',
            rule_type='priority', priority=1)
        # The second destination ranks first, so a plain call goes there.
        RuleDestination.objects.create(rule=rule, destination=SECOND, priority=1)
        RuleDestination.objects.create(rule=rule, destination=FIRST, priority=2)
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def earlier_call(self, hours_ago, destination=FIRST, status='completed'):
        self.n += 1
        row = CallLog.objects.create(
            organization=self.org, campaign=self.campaign, caller_number=CALLER,
            called_number='+15559990000', twilio_call_sid='old-%s' % self.n,
            status=status, destination_number=destination)
        CallLog.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(hours=hours_ago))

    def route(self):
        return RoutingEngine.route_call(
            str(self.campaign.id), {'caller_number': CALLER, 'twilio_call_sid': 'now'})

    def test_a_first_time_caller_is_routed_normally(self):
        self.assertEqual(self.route()['destination'], SECOND)

    def test_a_repeat_caller_goes_back_to_the_destination_that_took_them(self):
        self.earlier_call(hours_ago=2)
        self.assertEqual(self.route()['destination'], FIRST)

    def test_it_still_holds_long_after_the_duplicate_window(self):
        self.earlier_call(hours_ago=24 * 40)
        self.assertEqual(self.route()['destination'], FIRST)

    def test_it_holds_on_every_later_call(self):
        self.earlier_call(hours_ago=500)
        self.earlier_call(hours_ago=100)
        self.earlier_call(hours_ago=1)
        self.assertEqual(self.route()['destination'], FIRST)

    def test_a_first_call_nobody_answered_does_not_pin_the_caller(self):
        self.earlier_call(hours_ago=2, status='no_answer')
        self.assertEqual(self.route()['destination'], SECOND)

    def test_normal_still_routes_a_repeat_caller_like_any_call(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(duplicate_handling='normal')
        self.earlier_call(hours_ago=2)
        self.assertEqual(self.route()['destination'], SECOND)

    def test_new_campaigns_default_to_original(self):
        self.assertEqual(Campaign.objects.create(organization=self.org, name='N').duplicate_handling, 'original')
