"""CH-348: a TFN test started by hand passes only end to end - through the
carrier, into our system, routed, and a buyer's agent answers.

Run:  python manage.py test phone_numbers.test_tfn_end_to_end
"""
import json
from types import SimpleNamespace
from unittest import mock

from django.test import Client, override_settings
from django.utils import timezone

from analytics.models import CallRecord
from phone_numbers import tfn_test
from phone_numbers.models import TfnTest
from phone_numbers.test_tfn_test import SECRET, TEST_CID, Base
from routing.engine import RoutingEngine
from routing.models import CallLog

DEST = '+18883413566'
OK = {'destination': DEST, 'rule': None, 'error': None, 'buyer': SimpleNamespace(id=None, name='MTG')}
E2E = dict(TFN_TEST_RING_BUYER=True, TFN_TEST_REQUIRE_CARRIER=True, TFN_TEST_BUYER_RING_SECONDS=30)


@override_settings(**E2E)
class EndToEndTests(Base):
    def make(self, mode='real_call', trigger='manual'):
        return TfnTest.objects.create(
            organization=self.org, phone_number=self.phone, number=self.phone.number, requested_by=self.admin,
            status='running', mode=mode, trigger=trigger, started_at=timezone.now())

    def route(self):
        with mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
            r = self.inbound()
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def ended(self, call_id, answered, duration=0, dialstatus=''):
        return Client().post('/api/twilio/asterisk/call-ended/', content_type='application/json',
                             data=json.dumps({'call_log_id': call_id, 'answered': answered,
                                              'duration': duration, 'dialstatus': dialstatus}),
                             HTTP_X_ASTERISK_SECRET=SECRET)

    def keys(self, t):
        t.refresh_from_db()
        return [s['key'] for s in t.steps]

    def test_the_buyer_is_rung_and_an_answer_passes(self):
        t = self.make()
        body = self.route()
        self.assertEqual(body['action'], 'dial')
        self.assertEqual(body['buyer_number'], DEST)
        self.assertEqual(body['call_log_id'], f'tfntest-{t.pk}')
        self.assertEqual((body['max_duration'], body['recording_enabled']), (30, False))
        t.refresh_from_db()
        self.assertEqual(t.status, 'running')                # not passed until the buyer answers
        self.assertIn('buyer_ringing', self.keys(t))
        self.assertEqual(self.ended(body['call_log_id'], True, 2).status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.status, 'passed', t.reason)
        self.assertIn('the buyer answered', t.reason)
        self.assertEqual(self.keys(t)[-1], 'buyer')
        self.assertIn('through the carrier', t.steps[0]['text'])
        self.assertEqual(CallLog.objects.count(), 0)            # never a call row: no caps, no money
        rec = CallRecord.objects.get(twilio_call_sid=f'tfntest-{t.pk}')
        self.assertEqual((rec.status, rec.duration_seconds, rec.revenue, rec.caller_number),
                         ('completed', 2, 0, 'TFN Test'))

    def test_no_answer_fails(self):
        t = self.make()
        body = self.route()
        self.ended(body['call_log_id'], False, 0, 'NOANSWER')
        t.refresh_from_db()
        self.assertEqual(t.status, 'failed')
        self.assertIn("did not answer (no answer)", t.reason)
        self.assertEqual(CallRecord.objects.get().status, 'no_answer')

    def test_a_test_that_never_went_through_the_carrier_never_reads_passed(self):
        t = self.make(mode='internal_call')
        body = self.route()
        self.ended(body['call_log_id'], True, 2)
        t.refresh_from_db()
        self.assertEqual(t.status, 'failed')
        self.assertIn('carrier route could not be checked', t.reason)
        self.assertNotIn('End to end OK', t.reason)

    @override_settings(TFN_TEST_REQUIRE_CARRIER=False)
    def test_without_the_carrier_rule_the_buyer_answer_is_enough(self):
        t = self.make(mode='internal_call')
        body = self.route()
        self.ended(body['call_log_id'], True, 2)
        t.refresh_from_db()
        self.assertEqual(t.status, 'passed')

    def test_an_automatic_test_never_rings_a_buyer(self):
        t = self.make(trigger='auto')
        body = self.route()
        self.assertEqual(body['action'], 'hangup')
        t.refresh_from_db()
        self.assertEqual(t.status, 'passed')

    def test_a_routing_failure_does_not_ring_anyone(self):
        t = self.make()
        with mock.patch.object(RoutingEngine, 'route_call', return_value={'error': 'all buyers capped'}):
            body = self.inbound().json()
        self.assertEqual(body['action'], 'hangup')
        t.refresh_from_db()
        self.assertEqual(t.status, 'failed')

    def test_a_late_or_repeated_report_changes_nothing(self):
        t = self.make()
        body = self.route()
        self.ended(body['call_log_id'], True, 2)
        self.ended(body['call_log_id'], False, 0, 'NOANSWER')
        t.refresh_from_db()
        self.assertEqual(t.status, 'passed')
        self.assertEqual(CallRecord.objects.count(), 1)
        self.assertEqual(self.ended('tfntest-not-a-test', True, 2).status_code, 200)

    def test_the_relay_report_for_a_test_call_is_ignored(self):
        r = Client().post('/api/twilio/asterisk/held-answer/', content_type='application/json',
                          data=json.dumps({'call_log_id': 'tfntest-x', 'answered_at_epoch': 1}),
                          HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200)

    def test_when_the_carrier_refuses_it_runs_inside_and_says_so(self):
        t = TfnTest.objects.create(organization=self.org, phone_number=self.phone, number=self.phone.number,
                                   requested_by=self.admin, mode='real_call', trigger='manual')
        with mock.patch.object(tfn_test, '_real_call', return_value='the carrier refused the test call'), \
                mock.patch.object(tfn_test, '_internal_call', return_value=None) as internal:
            tfn_test.run(t.pk)
        internal.assert_called_once()
        t.refresh_from_db()
        self.assertEqual(t.mode, 'internal_call')
        self.assertEqual(t.steps[0]['key'], 'carrier')
        self.assertFalse(t.steps[0]['ok'])

    def test_the_button_tries_the_carrier_first(self):
        with mock.patch('tasks.run_tfn_test.delay'), mock.patch('phone_numbers.ami.configured', return_value=True):
            r = self.client.post('/api/numbers/check/test', data=json.dumps({'number': self.phone.number}),
                                 content_type='application/json')
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(TfnTest.objects.get().mode, 'real_call')
