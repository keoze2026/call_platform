"""CH-225: a buyer answer that crossed the caller hanging up counts, as on Revo.

Run:  python manage.py test routing.test_late_answer
"""
import json
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command

from routing.models import CallLog
from routing.test_call_cleanup import Base


class LateAnswerTests(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        self.retire = p.start()
        self.addCleanup(p.stop)
        self.campaign.revenue_amount = Decimal('1.00')
        self.campaign.min_call_duration = 0
        self.campaign.save()

    def missed(self, sid, caller='+16787331137', status=CallLog.Status.NO_ANSWER,
               reason='Not Connected', **extra):
        row = self.call(sid, caller=caller, age=60, status=status,
                        destination='+18779641512', **extra)
        CallLog.objects.filter(pk=row.pk).update(block_reason=reason)
        row.refresh_from_db()
        return row

    def event(self, row, answer_after=15.5):
        invite = row.created_at.timestamp() + 0.4
        return {'caller': row.caller_number.lstrip('+'), 'called': '18779641512',
                'invite_at': invite, 'cancel_at': invite + answer_after - 0.1,
                'answer_at': invite + answer_after}

    def run_cmd(self, events, *flags):
        out = StringIO()
        stdin = StringIO(''.join(json.dumps(e) + '\n' for e in events))
        with mock.patch('sys.stdin', stdin):
            call_command('mark_late_answers', *flags, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        row = self.missed('a')
        out = self.run_cmd([self.event(row)])
        self.assertIn('would be Completed, 1s', out)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_a_late_answer_is_completed_one_second_and_paid(self):
        row = self.missed('a')
        out = self.run_cmd([self.event(row)], '--apply')
        self.assertIn('1 marked', out)
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration, row.block_reason),
                         (CallLog.Status.COMPLETED, 1, ''))
        self.assertEqual(row.revenue, Decimal('1.00'))
        self.assertIsNotNone(row.answered_at)
        self.retire.delay.assert_called_once()

    def test_running_again_changes_nothing(self):
        row = self.missed('a')
        self.run_cmd([self.event(row)], '--apply')
        out = self.run_cmd([self.event(row)], '--apply')
        self.assertIn('0 still missed', out)
        self.retire.delay.assert_called_once()

    def test_another_callers_call_is_not_touched(self):
        row = self.missed('a')
        other = self.missed('b', caller='+15550001111')
        self.run_cmd([self.event(row)], '--apply')
        other.refresh_from_db()
        self.assertEqual(other.status, CallLog.Status.NO_ANSWER)

    def test_a_call_far_from_the_invite_time_is_not_touched(self):
        row = self.missed('a')
        ev = self.event(row)
        ev['invite_at'] += 60
        self.run_cmd([ev], '--apply')
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_a_blocked_or_refused_call_is_not_touched(self):
        row = self.missed('a', reason='Callerid Blocked')
        self.run_cmd([self.event(row)], '--apply')
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_a_hidden_earlier_try_is_left_alone(self):
        row = self.missed('a', retry_hidden=True)
        out = self.run_cmd([self.event(row)], '--apply')
        self.assertIn('left alone', out)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)
