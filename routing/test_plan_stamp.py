"""CH-471: a pause or play in the portal reaches Server A within seconds.

B gives a fingerprint of the plan's on/off settings; A's sync asks for it
every 5 seconds and fetches the plan as soon as it differs."""
from unittest import mock

from django.test import Client, override_settings

from buyers.destination import Destination
from campaigns.models import Campaign, CampaignCap
from phone_numbers.models import PhoneNumber
from routing.fallback import build_plan, plan_stamp
from routing.models import CallLog, RuleDestination
from routing.test_fallback import A, SECRET, TFN, FallbackBase, sync


class PlanStampTests(FallbackBase):
    def setUp(self):
        super().setUp()
        self.rd = self.line(A)

    def changes(self, fn):
        before = plan_stamp()
        fn()
        return plan_stamp() != before

    def test_still_while_calls_run(self):
        before = plan_stamp()
        CallLog.objects.create(organization=self.org, campaign=self.campaign, caller_number='+15551230000',
                               called_number=TFN, destination_number=A, status='in_progress',
                               twilio_call_sid='x1')
        self.assertEqual(plan_stamp(), before)

    def test_every_pause_and_play_moves_it(self):
        from accounts.models import User
        owner = User.objects.create_user(username='o@x.io', email='o@x.io', password='pw-123456',
                                         organization=self.org, is_superuser=True)
        from campaigns.services import CampaignService
        buyer = self.buyers['Q04']
        self.assertTrue(self.changes(lambda: CampaignService.pause(str(self.campaign.id), owner)))
        self.assertTrue(self.changes(lambda: CampaignService.activate(str(self.campaign.id), owner)))
        # bulk updates too: they skip save() and updated_at
        self.assertTrue(self.changes(lambda: type(buyer).objects.filter(pk=buyer.pk).update(status='paused')))
        self.assertTrue(self.changes(lambda: Destination.objects.filter(tfn=A).update(enabled=False)))
        self.assertTrue(self.changes(lambda: PhoneNumber.objects.filter(number=TFN).update(status='paused')))
        self.assertTrue(self.changes(lambda: RuleDestination.objects.filter(pk=self.rd.pk).update(weight=50)))
        self.assertTrue(self.changes(lambda: CampaignCap.objects.create(campaign=self.campaign, max_concurrency=5)))
        self.assertTrue(self.changes(lambda: Campaign.objects.filter(pk=self.campaign.pk).update(recording_enabled=False)))

    def test_plan_carries_its_stamp(self):
        self.assertEqual(build_plan()['stamp'], plan_stamp())

    @override_settings(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET)
    def test_endpoint(self):
        c = Client()
        url = '/api/twilio/asterisk/fallback-plan-stamp/'
        self.assertEqual(c.get(url).status_code, 403)
        r = c.get(url, HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'stamp': plan_stamp()})


class SyncFollowsStampTests(FallbackBase):
    """A's sync loop: the plan on the minute, and at once when the stamp moves."""

    def setUp(self):
        super().setUp()
        self.line(A)
        p = mock.patch.object(sync, 'held_stamp', None)
        p.start()
        self.addCleanup(p.stop)

    def run_loop(self, stamps, rounds):
        """Runs the sync loop for `rounds` rounds, 5 s apart. B's stamp answers
        come from `stamps`, one per stamp check. Returns the plan fetches."""
        clock = [1000.0]
        fetches = []
        answers = iter(stamps)

        def call_b(path, body=None, timeout=20):
            if path == sync.STAMP_PATH:
                return {'stamp': next(answers)}
            fetches.append(clock[0])
            return dict(build_plan(), stamp=f'S{len(fetches)}')

        def sleep(_):
            if len(sleeps) + 1 >= rounds:
                raise KeyboardInterrupt
            sleeps.append(1)
            clock[0] += 5

        sleeps = []
        with mock.patch.object(sync, 'call_b', side_effect=call_b), \
                mock.patch.object(sync, 'push_round'), mock.patch.object(sync, 'prune'), \
                mock.patch.object(sync.time, 'time', side_effect=lambda: clock[0]), \
                mock.patch.object(sync.time, 'sleep', side_effect=sleep), \
                mock.patch.object(sync.sys, 'argv', ['fallback_sync.py', '--loop']), \
                mock.patch.object(sync, 'log'):
            with self.assertRaises(KeyboardInterrupt):
                sync.main()
        return fetches

    def test_unchanged_settings_wait_for_the_minute(self):
        self.assertEqual(self.run_loop(['S1'] * 20, rounds=5), [1000.0])

    def test_a_change_is_fetched_within_one_round(self):
        # round 1 fetches (S1); rounds 2-3 see S1; round 4 sees the pause (S9)
        fetches = self.run_loop(['S1', 'S1', 'S9', 'S2', 'S2'], rounds=5)
        self.assertEqual(fetches, [1000.0, 1015.0])
        self.assertEqual(sync.held_stamp, 'S2')

    def test_b_without_the_stamp_keeps_the_minute(self):
        def plan_without_stamp(path, body=None, timeout=20):
            if path == sync.STAMP_PATH:
                raise AssertionError('must not ask for a stamp')
            return {k: v for k, v in build_plan().items() if k != 'stamp'}
        with mock.patch.object(sync, 'call_b', side_effect=plan_without_stamp):
            self.assertTrue(sync.fetch_plan())
        self.assertIsNone(sync.held_stamp)
        self.assertFalse(sync.plan_changed())

    def test_b_not_answering_is_no_change(self):
        sync.held_stamp = 'S1'
        with mock.patch.object(sync, 'call_b', side_effect=OSError('refused')):
            self.assertFalse(sync.plan_changed())
