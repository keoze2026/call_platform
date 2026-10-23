"""CH-241: on "Different", a repeat caller is never forwarded to a TFN that
already had him (Sir, 24 Oct: "in any case not a single call should be
forwarded on the same TFN").

Two holes, both tested end to end through the route request:
  - only ANSWERED calls counted as "already reached", so a caller whose first
    call rang out was sent straight back to that TFN;
  - after routing chose a buyer, the buyer's TFN rotation picked among all its
    live TFNs, ignoring the duplicate rule - so the repeat caller could be
    rotated back onto the TFN he already reached ("Original" likewise away
    from his original one).

Run:  python manage.py test routing.test_duplicates_never_same_tfn
"""
import json
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
CALLER = '+13072281061'
A, B, C = '+18667404516', '+18667404517', '+18667404518'


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class DifferentNeverSameTfnTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(
            organization=self.org, name='C-03', duplicate_handling='different',
            duplicate_direction='destination', duplicate_call_block_hours=24)
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        self.buyer = Buyer.objects.create(organization=self.org, name='PDSO')
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        # One buyer, three live TFNs - the rotation case. The rule lists them
        # without the + to prove numbers are compared by their digits.
        for i, n in enumerate((A, B, C), start=1):
            Destination.objects.create(organization=self.org, buyer=self.buyer, name=f'PDSO {i}',
                                       tfn=n, enabled=True)
            RuleDestination.objects.create(rule=rule, destination=n.lstrip('+'), buyer=self.buyer, priority=i)
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def dial(self):
        self.n += 1
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'):
            r = Client().post('/api/twilio/asterisk/route/',
                              data=json.dumps({'caller_number': CALLER, 'called_number': TFN,
                                               'asterisk_call_id': f'sid-{self.n}'}),
                              content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        return CallLog.objects.get(twilio_call_sid=f'sid-{self.n}')

    def end(self, call, status='no_answer', reason='Not Connected'):
        CallLog.objects.filter(pk=call.pk).update(status=status, block_reason=reason)

    def test_every_call_goes_to_a_tfn_he_has_not_reached(self):
        seen = []
        for _ in range(3):
            call = self.dial()
            self.assertNotIn(call.destination_number, seen, f'repeat on {call.destination_number}')
            seen.append(call.destination_number)
            self.end(call)  # rang out, nobody answered
        self.assertEqual(sorted(seen), sorted([A, B, C]))
        # Every TFN has had him: the fourth call is not forwarded at all.
        fourth = self.dial()
        self.assertEqual((fourth.status, fourth.block_reason, fourth.destination_number),
                         ('no_answer', 'Duplicate call', ''))

    def test_answered_calls_count_too(self):
        first = self.dial()
        self.end(first, status='completed', reason='')
        self.assertNotEqual(self.dial().destination_number, first.destination_number)

    def test_a_call_still_ringing_counts(self):
        first = self.dial()  # left in progress
        self.assertNotEqual(self.dial().destination_number, first.destination_number)

    def test_a_no_destination_refusal_does_not_count(self):
        CallLog.objects.create(organization=self.org, campaign=self.campaign, caller_number=CALLER,
                               called_number=TFN, twilio_call_sid='refused', status='no_answer',
                               block_reason='No Destination', destination_number=A)
        self.assertEqual(self.dial().destination_number, A)

    def test_outside_the_window_he_is_new_again(self):
        from datetime import timedelta
        from django.utils import timezone
        first = self.dial()
        self.end(first)
        CallLog.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(hours=25))
        self.assertIsNotNone(self.dial().destination_number)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RotationRespectsDuplicatesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.buyer = Buyer.objects.create(organization=self.org, name='PDSO')
        for n in (A, B, C):
            Destination.objects.create(organization=self.org, buyer=self.buyer, name=n, tfn=n, enabled=True)

    def test_rotation_never_lands_on_an_excluded_tfn(self):
        for _ in range(12):
            d = RoutingEngine.pick_live_destination(self.buyer, exclude={'8667404516'})
            self.assertNotEqual(d.tfn, A)

    def test_original_stays_on_its_tfn(self):
        for _ in range(6):
            self.assertEqual(RoutingEngine.pick_live_destination(self.buyer, only='8667404517').tfn, B)

    def test_nothing_left_hands_back_none(self):
        self.assertIsNone(RoutingEngine.pick_live_destination(
            self.buyer, exclude={'8667404516', '8667404517', '8667404518'}))

    def test_without_duplicates_rotation_is_unchanged(self):
        got = {RoutingEngine.pick_live_destination(self.buyer).tfn for _ in range(6)}
        self.assertEqual(got, {A, B, C})
