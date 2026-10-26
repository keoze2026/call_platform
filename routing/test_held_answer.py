"""CH-296: an answer that arrives while the relay holds our CANCEL is recorded
Answered, 1 second, whichever of the two reports arrives first.

Run:  python manage.py test routing.test_held_answer
"""
from decimal import Decimal
from unittest import mock

from django.utils import timezone

from routing.models import CallLog
from routing.test_call_cleanup import Base, ENDED

HELD = '/api/twilio/asterisk/held-answer/'


class HeldAnswerTests(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        p.start()
        self.addCleanup(p.stop)
        self.campaign.revenue_amount = Decimal('1.00')
        self.campaign.min_call_duration = 0
        self.campaign.save()

    def ringing(self):
        return self.call('a', caller='+18704239446', age=16, status=CallLog.Status.IN_PROGRESS,
                         destination='+18883413566')

    def held(self, row, at=None):
        return self.post(HELD, {'call_log_id': str(row.id),
                                'answered_at_epoch': at or timezone.now().timestamp()}).json()

    def cancelled(self, row):
        self.post(ENDED, {'call_log_id': str(row.id), 'duration': 0, 'answered': False,
                          'dialed_seconds': 0, 'dial_status': 'CANCEL'})
        row.refresh_from_db()
        return row

    def test_relay_first_then_hang_up_is_answered_one_second(self):
        row = self.ringing()
        self.assertEqual(self.held(row)['recorded'], 'pending')
        row = self.cancelled(row)
        self.assertEqual((row.status, row.duration, row.block_reason),
                         (CallLog.Status.COMPLETED, 1, ''))
        self.assertIn('Answered during cancel hold', row.tags)
        self.assertEqual(row.revenue, Decimal('1.00'))
        self.assertIsNotNone(row.answered_at)

    def test_hang_up_first_then_relay_is_answered_one_second(self):
        row = self.cancelled(self.ringing())
        self.assertEqual((row.status, row.block_reason), (CallLog.Status.NO_ANSWER, 'Not Connected'))
        self.assertEqual(self.held(row)['recorded'], 'completed')
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration), (CallLog.Status.COMPLETED, 1))
        self.assertIn('Answered during cancel hold', row.tags)

    def test_without_a_held_answer_a_cancel_stays_missed(self):
        row = self.cancelled(self.ringing())
        self.assertEqual((row.status, row.block_reason), (CallLog.Status.NO_ANSWER, 'Not Connected'))

    def test_a_held_answer_is_used_once(self):
        row = self.ringing()
        self.held(row)
        self.cancelled(row)
        second = self.call('b', caller='+18704239446', age=5, status=CallLog.Status.IN_PROGRESS,
                           destination='+18883413566')
        self.assertEqual(self.cancelled(second).status, CallLog.Status.NO_ANSWER)

    def test_needs_the_shared_secret(self):
        row = self.ringing()
        r = self.client.post(HELD, data='{"call_log_id": "%s"}' % row.id, content_type='application/json')
        self.assertEqual(r.status_code, 403)

    def test_an_answered_call_is_left_alone(self):
        row = self.call('c', age=100, status=CallLog.Status.COMPLETED, destination='+18883413566')
        CallLog.objects.filter(pk=row.pk).update(duration=80)
        self.assertEqual(self.held(row)['recorded'], False)
        row.refresh_from_db()
        self.assertEqual(row.duration, 80)
