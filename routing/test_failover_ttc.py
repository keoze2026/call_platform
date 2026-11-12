"""CH-672: after a call moves to the next buyer, each buyer's ring time is
its own.

12 Nov, HOZ: "you are lying - send SIP". 5619328838, 10:31:06 AM EST: KOZ got
the call first and rang 15 s; it moved to HOZ at 10:31:22; the caller hung up
at 10:31:28, before HOZ's phone even rang. Our Call Log showed HOZ, ring 21 s
(the caller's whole wait), and kept KOZ's miss with 0 s.

Run:  python manage.py test routing.test_failover_ttc
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from routing.models import CallLog
from routing.test_failover import A, B, ON, SECRET, FailoverTests


@override_settings(**ON)
class FailoverRingTimeTests(TestCase):
    setUp = FailoverTests.setUp
    post = FailoverTests.post
    dial = FailoverTests.dial
    next = FailoverTests.next

    def end(self, call_id):
        with mock.patch('tasks.retire_used_number.delay'), mock.patch('tasks.enrich_call_carrier.delay'), \
                mock.patch('tasks.charge_rejected_call.delay'):
            r = Client().post('/api/twilio/asterisk/call-ended/', data=json.dumps(
                {'call_log_id': call_id, 'duration': 0, 'answered': False, 'recording_url': '',
                 'dialed_seconds': 0, 'dial_status': 'CANCEL'}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)

    def test_each_buyer_keeps_its_own_ring_time(self):
        first = self.dial(caller='+15619328838')
        came_in = timezone.now() - timedelta(seconds=21)
        CallLog.objects.filter(id=first['call_log_id']).update(created_at=came_in)
        # The first buyer (Q06) rang 15 s and missed; Asterisk sends 0 for an
        # unanswered dial. The call moves to R48.
        nxt = self.next(first, 'NOANSWER', seconds=0)
        self.assertEqual(nxt['buyer_number'], B)
        miss = CallLog.objects.get(retry_hidden=True)
        self.assertEqual(miss.destination_number, A)
        self.assertAlmostEqual(miss.ring_seconds, 21, delta=1)          # not 0
        self.assertEqual(miss.created_at, came_in)                       # when it was dialled
        # R48 was dialled 6 s ago; the caller hangs up now.
        CallLog.objects.filter(pk=miss.pk).update(ended_at=timezone.now() - timedelta(seconds=6))
        self.end(first['call_log_id'])
        call = CallLog.objects.get(id=first['call_log_id'])
        self.assertEqual((call.buyer.name, call.status), ('R48', 'no_answer'))
        self.assertAlmostEqual(call.ring_seconds, 6, delta=1)           # not 21

    def test_a_call_on_its_first_buyer_counts_from_when_it_came_in(self):
        first = self.dial()
        CallLog.objects.filter(id=first['call_log_id']).update(created_at=timezone.now() - timedelta(seconds=12))
        self.end(first['call_log_id'])
        self.assertAlmostEqual(CallLog.objects.get(id=first['call_log_id']).ring_seconds, 12, delta=1)

    def test_the_repair_command_corrects_calls_from_before(self):
        from io import StringIO

        from django.core.management import call_command
        now = timezone.now()
        came_in, moved, ended = now - timedelta(seconds=22), now - timedelta(seconds=6), now
        q06, r48 = self.buyers['Q06'], self.buyers['R48']
        call = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=r48,
                                      caller_number='+15619328838', called_number='+18888910343',
                                      destination_number=B, twilio_call_sid='hoz', status='no_answer',
                                      ring_seconds=22, ended_at=ended)
        CallLog.objects.filter(pk=call.pk).update(created_at=came_in)
        miss = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=q06,
                                      caller_number='+15619328838', called_number='+18888910343',
                                      destination_number=A, twilio_call_sid='hoz#try1', status='no_answer',
                                      block_reason='No Answer', ring_seconds=0, ended_at=moved,
                                      retry_hidden=True, retry_root_id=call.pk)
        CallLog.objects.filter(pk=miss.pk).update(created_at=moved)
        out = StringIO()
        call_command('fix_failover_ttc', stdout=out)                      # dry run changes nothing
        self.assertIn('1 call(s) would be corrected', out.getvalue())
        self.assertEqual(CallLog.objects.get(pk=call.pk).ring_seconds, 22)
        call_command('fix_failover_ttc', '--apply', stdout=StringIO())
        self.assertEqual(CallLog.objects.get(pk=call.pk).ring_seconds, 6)
        m = CallLog.objects.get(pk=miss.pk)
        self.assertEqual((m.ring_seconds, m.created_at), (16, came_in))
