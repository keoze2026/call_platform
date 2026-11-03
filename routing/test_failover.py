"""CH-514: a destination that does not answer passes the call to the next
buyer, and a destination that keeps missing rests 15 minutes.

Sir, 4 Nov: "If a destination doesn't answer within about 15 seconds, the call
moves to the next buyer straight away. The caller is never lost. After 5
misses in a row: cooldown ... 15 minutes ... then one call. If that's answered,
the destination goes back to normal. If not, another 15-minute cooldown."

Run:  python manage.py test routing.test_failover
"""
import json
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
A, B, C = '+18667404516', '+18667404517', '+18667404518'
ON = dict(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, FAILOVER_RING_SECONDS=15,
          LINE_COOLDOWN_MISSES=5, LINE_COOLDOWN_MINUTES=15,
          CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})


@override_settings(**ON)
class FailoverTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-07', status='active')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        self.buyers = {}
        for i, (name, n) in enumerate((('Q06', A), ('R48', B), ('Q04', C)), start=1):
            b = Buyer.objects.create(organization=self.org, name=name)
            self.buyers[name] = b
            Destination.objects.create(organization=self.org, buyer=b, name=f'{name}|{n[-4:]}|',
                                       tfn=n, enabled=True)
            RuleDestination.objects.create(rule=rule, destination=n, buyer=b, priority=i)
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def post(self, path, body):
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'):
            r = Client().post(path, data=json.dumps(body), content_type='application/json',
                              HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def dial(self, caller=None):
        self.n += 1
        return self.post('/api/twilio/asterisk/route/', {
            'caller_number': caller or f'+1555000{self.n:04d}', 'called_number': TFN,
            'asterisk_call_id': f'sid-{self.n}'})

    def next(self, out, status='NOANSWER', seconds=15):
        return self.post('/api/twilio/asterisk/route-next/', {
            'call_log_id': out['call_log_id'], 'dial_status': status, 'dialed_seconds': seconds})

    def test_route_reply_carries_the_ring_time(self):
        out = self.dial()
        self.assertEqual((out['action'], out['buyer_number'], out['ring_timeout'], out['next_on_fail']),
                         ('dial', A, 15, 1))

    def test_no_answer_moves_to_the_next_buyer_on_the_same_call(self):
        first = self.dial()
        nxt = self.next(first)
        self.assertEqual((nxt['action'], nxt['buyer_number'], nxt['call_log_id']), ('dial', B, first['call_log_id']))
        call = CallLog.objects.get(id=first['call_log_id'])
        self.assertEqual((call.destination_number, call.buyer.name, call.status, call.retry_hidden),
                         (B, 'R48', 'in_progress', False))
        miss = CallLog.objects.get(retry_hidden=True)
        self.assertEqual((miss.destination_number, miss.buyer.name, miss.status, miss.block_reason, miss.retry_root_id),
                         (A, 'Q06', 'no_answer', 'No Answer', call.id))
        # Busy on R48: Q04 next; then nothing left - the call ends as it is.
        self.assertEqual(self.next(nxt, 'BUSY')['buyer_number'], C)
        last = self.next(nxt, 'CHANUNAVAIL')
        self.assertEqual(last['action'], 'hangup')
        self.assertEqual(CallLog.objects.get(id=first['call_log_id']).destination_number, C)
        self.assertEqual(CallLog.objects.filter(retry_hidden=True).count(), 2)
        # One visible row for the caller's call.
        self.assertEqual(CallLog.objects.filter(retry_hidden=False).count(), 1)

    def test_caller_hung_up_or_answered_nothing_more(self):
        first = self.dial()
        self.assertEqual(self.next(first, 'CANCEL')['action'], 'hangup')
        self.assertEqual(self.next(first, 'ANSWER')['action'], 'hangup')
        self.assertFalse(CallLog.objects.filter(retry_hidden=True).exists())

    @override_settings(FAILOVER_RING_SECONDS=0)
    def test_off_changes_nothing(self):
        first = self.dial()
        self.assertNotIn('ring_timeout', first)
        self.assertEqual(self.next(first)['action'], 'hangup')
        self.assertFalse(CallLog.objects.filter(retry_hidden=True).exists())

    def miss_on_a(self, n, minutes_ago=1):
        when = timezone.now() - timedelta(minutes=minutes_ago)
        for i in range(n):
            c = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyers['Q06'],
                                       caller_number=f'+1666000{i:04d}', called_number=TFN, destination_number=A,
                                       twilio_call_sid=f'old-{minutes_ago}-{i}', status='no_answer',
                                       block_reason='No Answer', ended_at=when)
            CallLog.objects.filter(pk=c.pk).update(created_at=when - timedelta(seconds=20))

    def test_five_misses_in_a_row_rest_the_destination(self):
        self.miss_on_a(4)
        self.assertEqual(self.dial()['buyer_number'], A)         # 4 misses: still tried
        CallLog.objects.filter(destination_number=A, status='in_progress').update(
            status='no_answer', block_reason='No Answer', ended_at=timezone.now())
        out = self.dial()                                       # 5 in a row: resting
        self.assertEqual(out['buyer_number'], B)
        trace = CallLog.objects.get(id=out['call_log_id']).routing_trace
        self.assertTrue(any('resting' in (d.get('reason') or '') for d in trace['rejected_destinations']))

    def test_after_the_rest_one_call_then_back_to_normal_if_answered(self):
        self.miss_on_a(5, minutes_ago=16)
        probe = self.dial()
        self.assertEqual(probe['buyer_number'], A)              # the one test call
        self.assertEqual(self.dial()['buyer_number'], B)        # while it is on, A rests
        CallLog.objects.filter(id=probe['call_log_id']).update(status='completed', ended_at=timezone.now())
        self.assertEqual(self.dial()['buyer_number'], A)        # answered: normal again

    def test_after_the_rest_a_missed_test_call_rests_it_again(self):
        self.miss_on_a(5, minutes_ago=16)
        probe = self.dial()
        CallLog.objects.filter(id=probe['call_log_id']).update(
            status='no_answer', block_reason='No Answer', ended_at=timezone.now())
        self.assertEqual(self.dial()['buyer_number'], B)

    def test_caller_hanging_up_does_not_count_as_a_miss(self):
        self.miss_on_a(4)
        c = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyers['Q06'],
                                   caller_number='+17770000000', called_number=TFN, destination_number=A,
                                   twilio_call_sid='hung', status='no_answer', block_reason='Not Connected',
                                   ended_at=timezone.now())
        self.assertTrue(c.pk)
        self.assertEqual(self.dial()['buyer_number'], A)

    def test_next_buyer_is_not_refused_as_a_duplicate(self):
        first = self.dial('+13072281061')
        Campaign.objects.filter(pk=self.campaign.pk).update(duplicate_call_block=True, duplicate_call_block_hours=24)
        self.assertEqual(self.next(first)['buyer_number'], B)


class ServerAPartsTests(TestCase):
    """The two Server A pieces: the AGI script and the dialplan change."""

    def load(self, path, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_agi_sets_the_ring_time_only_when_b_sends_it(self):
        rc = self.load('scripts/fallback/route_call.py', 'route_call_ch514')
        sent = []
        with mock.patch.object(rc, 'agi_cmd', side_effect=lambda c: sent.append(c) or '200 result=1'):
            rc.set_dial_vars({'buyer_number': A, 'call_log_id': 'x', 'ring_timeout': 15, 'next_on_fail': 1})
            self.assertIn('SET VARIABLE RING_TIMEOUT "15"', sent)
            self.assertIn('SET VARIABLE NEXT_ON_FAIL "1"', sent)
            sent.clear()
            rc.set_dial_vars({'buyer_number': A, 'call_log_id': 'x'})      # failover off / Server A routing
            self.assertIn('SET VARIABLE RING_TIMEOUT ""', sent)
            self.assertIn('SET VARIABLE NEXT_ON_FAIL "0"', sent)
        with mock.patch.object(rc, 'agi_cmd', return_value='200 result=1 (NOANSWER)'):
            self.assertEqual(rc.agi_get_var('DIALSTATUS'), 'NOANSWER')
        with mock.patch.object(rc, 'agi_cmd', return_value='200 result=0'):
            self.assertEqual(rc.agi_get_var('DIALEDTIME'), '')

    def test_dialplan_change_on_the_reference_from_carrier(self):
        fd = self.load('deploy/failover_dialplan.py', 'failover_dialplan_ch514')
        src = open('docs/asterisk/from-carrier-no-early-answer.conf').read()
        out = fd.transform(src)
        self.assertIn('n(redial),Set(DIAL_NUM=', out)
        self.assertIn('${IF($["${RING_TIMEOUT}" != ""]?${RING_TIMEOUT}:${MAX_DURATION})}', out)
        self.assertIn('AGI(route_call.py,next)', out)
        self.assertIn('n(nofailover),Hangup(', out)
        with self.assertRaises(fd.Stop):
            fd.transform(out)                         # never applied twice

    def test_dialplan_change_on_server_a_as_it_is(self):
        # Server A's real [from-carrier] (4 Nov): the endpoint is ${OUT_EP}
        # (cancel-hold) and Dial() runs a pre-dial handler.
        fd = self.load('deploy/failover_dialplan.py', 'failover_dialplan_ch516')
        src = open('docs/asterisk/server-a-from-carrier-2026-11-04.conf').read()
        out = fd.transform(src)
        self.assertIn('n(redial),Set(DIAL_NUM=', out)
        self.assertIn('Dial(PJSIP/${DIAL_NUM}@${OUT_EP},${IF($["${RING_TIMEOUT}" != ""]?${RING_TIMEOUT}:${MAX_DURATION})},'
                      'grb(avx-out-hdr^s^1(${CALL_LOG_ID})))', out)
        self.assertIn('AGI(route_call.py,next)', out)
        self.assertIn('n(nofailover),Hangup(', out)
        self.assertIn('n(nobuyer),Hangup(21)', out)

