"""CH-312: a TFN test call shows in the reports with caller ID "Test"."""
from unittest import mock

from analytics.models import CallRecord
from phone_numbers import tfn_test
from phone_numbers.models import PhoneNumber, TfnTest
from phone_numbers.test_tfn_test import NO_BUYER, OK, Base
from publishers.models import Publisher
from routing.engine import RoutingEngine
from routing.models import CallLog


class TestCallInReportsTests(Base):
    def setUp(self):
        super().setUp()
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO')
        PhoneNumber.objects.filter(pk=self.phone.pk).update(publisher=self.pub)

    def test_a_passed_test_is_one_report_row_with_caller_test(self):
        t = self.running_real_test()
        with mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
            self.inbound()
        rows = list(CallRecord.objects.all())
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r.caller_number, r.called_number), ('Test', '+18774893792'))
        self.assertEqual((r.block_reason, r.status), ('TFN Test', 'no_answer'))
        self.assertEqual((r.campaign_name, r.publisher_name, r.buyer_name), ('C-03', 'PDSO', 'Acme'))
        self.assertEqual(r.destination_number, '15557654321')
        self.assertEqual((r.duration_seconds, r.revenue, r.payout, r.platform_cost), (0, 0, 0, 0))
        self.assertEqual(r.twilio_call_sid, f'tfntest-{t.pk}')
        # Nothing in CallLog: no cap, no alert, no Revo matching, no billing.
        self.assertEqual(CallLog.objects.count(), 0)

    def test_a_failed_test_shows_why(self):
        self.running_real_test()
        PhoneNumber.objects.filter(pk=self.phone.pk).update(campaign=None)
        self.inbound()
        r = CallRecord.objects.get()
        self.assertTrue(r.block_reason.startswith('TFN Test failed: '))
        self.assertIn('campaign', r.block_reason)
        self.assertEqual(r.buyer_name, '')

    def test_routing_that_would_refuse_gives_no_buyer(self):
        self.running_real_test()
        refused = {'destination': None, 'rule': None, 'error': 'Cap reached'}
        with mock.patch.object(RoutingEngine, 'route_call', return_value=refused):
            self.inbound()
        r = CallRecord.objects.get()
        self.assertIn('Cap reached', r.block_reason)
        self.assertEqual((r.buyer_name, r.destination_number), ('', ''))

    def test_one_row_per_test(self):
        t = self.running_real_test()
        phone = PhoneNumber.objects.get(pk=self.phone.pk)
        tfn_test.record_test_call(t, phone, phone.number, True, 'ok', OK)
        tfn_test.record_test_call(t, phone, phone.number, True, 'ok', OK)
        self.assertEqual(CallRecord.objects.count(), 1)

    def test_a_normal_call_from_the_test_number_is_not_marked_test(self):
        with mock.patch.object(RoutingEngine, 'route_call', return_value=NO_BUYER):
            self.inbound()
        self.assertFalse(CallRecord.objects.filter(caller_number='Test').exists())
        self.assertEqual(TfnTest.objects.count(), 0)
