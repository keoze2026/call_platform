"""CH-360: the carrier watch - a carrier that stops delivering (its credit ran
out, as on 28 Oct) turns its TFNs to Not Working and alerts the team.

Run:  python manage.py test phone_numbers.test_carrier_watch
"""
from datetime import datetime, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from campaigns.models import Campaign
from phone_numbers import carrier_watch as W
from phone_numbers import tfn_check
from phone_numbers.models import Carrier, PhoneNumber, TfnTest
from publishers.models import Publisher

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
NOON_ET = datetime(2026, 10, 29, 12, 0, tzinfo=W.ET)


@override_settings(CACHES=LOCMEM, TFN_TEST_CALLER_ID='+18337415406', CARRIER_WATCH_ENABLED=True,
                   CARRIER_TEST_CALLS=True)
class CarrierWatchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='C-13', status='active')
        self.freedom = Carrier.objects.create(organization=self.org, name='Freedom', code='FRD')
        self.pub = Publisher.objects.create(organization=self.org, name='ADS-1')
        self.stock = PhoneNumber.objects.create(organization=self.org, number='+18888913600', vendor='Other',
                                                status='active', carrier=self.freedom, campaign=self.camp)
        self.used = PhoneNumber.objects.create(organization=self.org, number='+18888913606', vendor='Other',
                                               status='active', carrier=self.freedom, campaign=self.camp,
                                               publisher=self.pub)
        for p in (mock.patch('phone_numbers.ami.configured', return_value=True),
                  mock.patch('tasks.run_tfn_test.delay')):
            p.start()
            self.addCleanup(p.stop)
        self.sent = []

    def tick(self, minutes=0):
        return W.tick(self.sent.append, now=NOON_ET + timedelta(minutes=minutes))

    def finish(self, reached, reason=''):
        """The watch test running now ends: came in through the carrier, or not."""
        t = TfnTest.objects.get(pk=cache.get(W.TEST_KEY.format(self.freedom.id)))
        steps = [{'key': 'reached', 'ok': True, 'text': 'Came in through the carrier'}] if reached else []
        TfnTest.objects.filter(pk=t.pk).update(
            status='passed' if reached else 'failed', steps=steps, finished_at=timezone.now(),
            reason=reason or ('Call reached the platform.' if reached else
                              'No call reached the platform within 30s. The carrier did not deliver this number to us.'))
        return t

    def test_tests_a_number_with_no_publisher_first(self):
        self.assertEqual(self.tick(), {'FRD': 'testing +18888913600'})
        t = TfnTest.objects.get()
        self.assertEqual((t.trigger, t.mode), ('auto', 'real_call'))

    def test_a_delivering_carrier_is_ok(self):
        self.tick()
        self.finish(True)
        self.tick(5)
        self.assertEqual(W.state(self.freedom.id)['status'], 'ok')
        self.assertEqual(self.sent, [])

    def test_two_missed_test_calls_mark_the_carrier_down_and_alert_once(self):
        self.tick()
        self.finish(False)
        self.assertEqual(self.tick(5)['FRD'], 'testing +18888913606')    # one miss: test again at once
        self.assertEqual(self.sent, [])
        self.finish(False)
        self.tick(10)
        self.assertEqual(W.state(self.freedom.id)['status'], 'down')
        self.assertEqual(len(self.sent), 1)
        self.assertIn('carrier NOT delivering', self.sent[0])
        self.assertIn('Freedom (FRD)', self.sent[0])
        self.assertIn('2 live TFNs now show Not Working', self.sent[0])

    def test_back_to_delivering_is_announced(self):
        self.tick()
        self.finish(False)
        self.tick(5)
        self.finish(False)
        self.tick(10)
        cache.delete(f'carrier_watch:last_start:{self.freedom.id}')
        self.tick(15)
        self.finish(True)
        self.tick(20)
        self.assertEqual(W.state(self.freedom.id)['status'], 'ok')
        self.assertIn('carrier delivering again', self.sent[-1])

    def test_our_test_line_failing_never_marks_a_carrier_down(self):
        self.tick()
        t = self.finish(False, reason='Real call not placed (the carrier refused the test call before it rang). '
                                      'Platform check only: ok')
        TfnTest.objects.filter(pk=t.pk).update(mode='platform_check')
        self.tick(5)
        self.assertEqual(W.state(self.freedom.id)['status'], 'unknown')
        self.assertIn('could not place the test call', self.sent[0])
        self.assertIn('not marked down', self.sent[0])

    def test_no_test_calls_outside_7_30_am_to_9_pm_eastern(self):
        early = datetime(2026, 10, 29, 6, 0, tzinfo=W.ET)
        self.assertEqual(W.tick(self.sent.append, now=early), {'FRD': 'outside 7:30 AM - 9 PM ET'})
        self.assertFalse(TfnTest.objects.exists())

    def test_one_test_per_carrier_every_15_minutes(self):
        self.tick()
        self.finish(True)
        self.tick(5)                               # settles, not due yet
        self.assertEqual(TfnTest.objects.count(), 1)
        self.tick(16)
        self.assertEqual(TfnTest.objects.count(), 2)

    @override_settings(CARRIER_WATCH_ENABLED=False)
    def test_can_be_switched_off(self):
        self.assertEqual(self.tick(), {'watch': 'off'})


@override_settings(CACHES=LOCMEM)
class TfnCheckUsesTheWatchTests(TestCase):
    """What TFN Check says once the watch knows the carrier is down or fine."""

    def setUp(self):
        cache.clear()
        ready = mock.patch.object(tfn_check, '_ready', return_value=(True, ''))
        ready.start()
        self.addCleanup(ready.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='C-13', status='active')
        self.freedom = Carrier.objects.create(organization=self.org, name='Freedom', code='FRD')
        PhoneNumber.objects.create(organization=self.org, number='+18888913606', vendor='Other',
                                   status='active', carrier=self.freedom, campaign=self.camp)
        from routing.models import CallLog
        CallLog.objects.create(organization=self.org, campaign=self.camp, caller_number='+15551234567',
                               called_number='+18888913606', twilio_call_sid='sid-1', status='completed')

    def set_state(self, status, minutes_ago=0):
        cache.set(W.STATE_KEY.format(self.freedom.id),
                  {'status': status, 'at': (timezone.now() - timedelta(minutes=minutes_ago)).isoformat(),
                   'detail': 'test calls did not come in (last at 3:05 PM ET)'})

    def result(self, staff=False):
        return tfn_check.check(self.org, '8888913606', staff=staff)['results'][0]

    def test_a_carrier_down_makes_every_tfn_not_working_even_with_a_call_just_now(self):
        self.set_state('down')
        r = self.result(staff=True)
        self.assertEqual((r['status'], r['detail']), ('not_working', 'Calls to this number are not reaching us right now.'))
        self.assertIn('Carrier FRD is not delivering calls', r['why'])
        self.assertNotIn('why', self.result())          # never on a share link or a publisher login
        self.assertNotIn('Freedom', self.result()['detail'])

    def test_an_old_watch_result_is_not_trusted(self):
        self.set_state('down', minutes_ago=90)
        self.assertEqual(self.result()['status'], 'working')     # the call just now

    def test_a_delivering_carrier_proves_a_quiet_number(self):
        from routing.models import CallLog
        CallLog.objects.all().delete()
        self.set_state('ok', minutes_ago=10)
        r = self.result()
        self.assertEqual(r['status'], 'working')
        self.assertEqual(r['detail'], 'The carrier delivered a test call 10 minutes ago.')
        self.assertIn('carrier_checked_at', r)


@override_settings(CACHES=LOCMEM, CARRIER_WATCH_ENABLED=True, TFN_TEST_CALLER_ID='', CARRIER_SILENCE_MIN_CALLS=10)
class SilenceWatchTests(TestCase):
    """CH-361: busy TFNs that suddenly get no calls = the carrier stopped
    delivering (its recharge ran out, as on 28 Oct)."""

    def setUp(self):
        cache.clear()
        from routing.models import CallLog
        self.CallLog = CallLog
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='C-13', status='active')
        self.freedom = Carrier.objects.create(organization=self.org, name='Freedom', code='KMQ')
        self.xlr = Carrier.objects.create(organization=self.org, name='XLR', code='XLR')
        self.k1 = PhoneNumber.objects.create(organization=self.org, number='+18888913606', vendor='Other',
                                             status='active', carrier=self.freedom, campaign=self.camp)
        self.k2 = PhoneNumber.objects.create(organization=self.org, number='+18888913604', vendor='Other',
                                             status='active', carrier=self.freedom, campaign=self.camp)
        self.x1 = PhoneNumber.objects.create(organization=self.org, number='+18552026099', vendor='Other',
                                             status='active', carrier=self.xlr, campaign=self.camp)
        self.x2 = PhoneNumber.objects.create(organization=self.org, number='+18552026098', vendor='Other',
                                             status='active', carrier=self.xlr, campaign=self.camp)
        self.now = NOON_ET
        self.n = 0
        self.sent = []

    def calls(self, phone, count, minutes_ago):
        for _ in range(count):
            self.n += 1
            c = self.CallLog.objects.create(organization=self.org, campaign=self.camp, caller_number='+15550000000',
                                            called_number=phone.number, twilio_call_sid=f's{self.n}', status='completed')
            self.CallLog.objects.filter(pk=c.pk).update(created_at=self.now - timedelta(minutes=minutes_ago))

    def tick(self, minutes=0):
        return W.tick(self.sent.append, now=self.now + timedelta(minutes=minutes))

    def test_busy_carrier_going_quiet_is_down_and_alerts_once(self):
        self.calls(self.k1, 6, 60)
        self.calls(self.k2, 6, 90)          # Freedom: 12 calls before, none in the last 20 min
        self.calls(self.x1, 6, 60)
        self.calls(self.x2, 6, 70)
        self.calls(self.x1, 2, 5)           # XLR still getting calls
        self.tick()
        self.assertEqual(W.state(self.freedom.id)['status'], 'down')
        self.assertNotEqual(W.state(self.xlr.id).get('status'), 'down')
        self.assertEqual(len(self.sent), 1)
        self.assertIn('carrier went silent', self.sent[0])
        self.assertIn('Freedom (KMQ)', self.sent[0])
        self.tick(5)
        self.assertEqual(len(self.sent), 1)  # not repeated
        r = tfn_check.check(self.org, '8888913606', staff=True)['results'][0]
        self.assertEqual(r['status'], 'not_working')
        self.assertIn('no calls on any of its 2 busy TFNs', r['why'])

    def test_tfns_on_a_paused_campaign_or_none_are_still_watched(self):
        # 29 Oct on the server: busy TFNs sat on paused campaigns or none.
        self.camp.status = 'paused'
        self.camp.save(update_fields=['status'])
        PhoneNumber.objects.filter(pk=self.k2.pk).update(campaign=None)
        self.calls(self.k1, 6, 60)
        self.calls(self.k2, 6, 90)
        self.calls(self.x1, 6, 60)
        self.calls(self.x2, 6, 70)
        self.calls(self.x1, 2, 5)
        self.tick()
        self.assertEqual(W.state(self.freedom.id)['status'], 'down')
        self.assertIn('Freedom (KMQ)', self.sent[0])

    @override_settings(CARRIER_SILENCE_MIN_CALLS=30)
    def test_a_quiet_evening_is_not_an_outage(self):
        # CH-363, 29 Oct 8:24 PM ET: 14 calls in 2 hours, then 20 minutes
        # without one, was flagged. At the default of 30 it is left alone.
        self.calls(self.k1, 7, 60)
        self.calls(self.k2, 7, 90)
        self.calls(self.x1, 2, 5)
        self.tick()
        self.assertEqual(W.state(self.freedom.id), {})
        self.assertEqual(self.sent, [])

    def test_the_first_call_clears_it(self):
        self.calls(self.k1, 6, 60)
        self.calls(self.k2, 6, 90)
        self.calls(self.x1, 6, 30)
        self.calls(self.x2, 6, 30)
        self.calls(self.x1, 1, 3)
        self.tick()
        self.calls(self.k1, 1, -4)          # a call comes in 4 minutes later
        self.tick(5)
        self.assertNotEqual(W.state(self.freedom.id)['status'], 'down')
        self.assertIn('calls coming in again', self.sent[-1])

    def test_a_quiet_number_with_little_traffic_is_left_alone(self):
        self.calls(self.k1, 4, 60)          # under the 10-call minimum
        self.tick()
        self.assertEqual(W.state(self.freedom.id), {})
        self.assertEqual(self.sent, [])

    def test_every_carrier_quiet_at_once_blames_our_side_not_the_carriers(self):
        self.calls(self.k1, 6, 60)
        self.calls(self.k2, 6, 60)
        self.calls(self.x1, 6, 60)
        self.calls(self.x2, 6, 60)
        self.tick()
        self.assertEqual(W.state(self.freedom.id), {})
        self.assertEqual(W.state(self.xlr.id), {})
        self.assertIn('no calls on ANY carrier', self.sent[0])

    def test_runs_even_when_test_calls_are_not_possible(self):
        self.calls(self.k1, 6, 60)
        self.calls(self.k2, 6, 60)
        out = self.tick()
        self.assertEqual(out.get('KMQ (calls)'), 'silent')
        self.assertIn(out.get('watch'), ('test calls off', 'test calls not configured'))

    def test_only_from_7_30_am_to_9_pm_eastern(self):
        self.calls(self.k1, 6, 60)
        self.calls(self.k2, 6, 60)
        W.tick(self.sent.append, now=datetime(2026, 10, 29, 22, 0, tzinfo=W.ET))
        self.assertEqual(W.state(self.freedom.id), {})


@override_settings(CACHES=LOCMEM, TFN_TEST_CALLER_ID='+18337415406', CARRIER_WATCH_ENABLED=True)
class NoAutomaticTestCallsTests(TestCase):
    """CH-366, Sir 29 Oct: "Who is running automatic test - hope this will
    not kill TFN". By default the watch places no test calls at all."""

    def test_no_test_call_is_placed_by_default(self):
        org = Organization.objects.create(name='Avortyx', slug='avx')
        camp = Campaign.objects.create(organization=org, name='C-13', status='active')
        carrier = Carrier.objects.create(organization=org, name='Freedom', code='KMQ')
        PhoneNumber.objects.create(organization=org, number='+18888913606', vendor='Other',
                                   status='active', carrier=carrier, campaign=camp)
        with mock.patch('phone_numbers.ami.configured', return_value=True):
            out = W.tick(lambda text: None, now=NOON_ET)
        self.assertEqual(out['watch'], 'test calls off')
        self.assertFalse(TfnTest.objects.exists())
