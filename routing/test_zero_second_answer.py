"""CH-187: an answer with 0 seconds of talk is Not Connected, as in Revo.

Run:  python manage.py test routing.test_zero_second_answer
"""
from datetime import timedelta
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from analytics.models import CallRecord
from routing.models import CallLog
from routing.test_call_cleanup import Base, ENDED


class ZeroSecondAnswerTests(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        self.retire = p.start()
        self.addCleanup(p.stop)

    def end(self, row, duration, answered=True, **extra):
        body = {'call_log_id': str(row.id), 'duration': duration, 'answered': answered,
                'recording_url': '', 'dialed_seconds': 9, 'dial_status': 'ANSWER'}
        body.update(extra)
        r = self.post(ENDED, body)
        row.refresh_from_db()
        return r.json()

    def test_zero_seconds_is_not_connected(self):
        row = self.call('a')
        out = self.end(row, 0)
        self.assertEqual((row.status, row.duration, row.block_reason),
                         (CallLog.Status.NO_ANSWER, 0, 'Not Connected'))
        self.assertFalse(out['converted'])
        self.assertEqual(row.ring_seconds, 9)
        self.retire.delay.assert_not_called()

    def test_one_second_is_connected(self):
        row = self.call('a')
        self.end(row, 1)
        self.assertEqual((row.status, row.duration, row.block_reason),
                         (CallLog.Status.COMPLETED, 1, ''))
        self.retire.delay.assert_called_once()

    def test_a_real_conversation_is_unchanged(self):
        row = self.call('a')
        self.end(row, 255)
        self.assertEqual((row.status, row.duration), (CallLog.Status.COMPLETED, 255))

    def test_an_unanswered_call_is_unchanged(self):
        row = self.call('a')
        self.end(row, 0, answered=False, dial_status='CANCEL')
        self.assertEqual((row.status, row.block_reason), (CallLog.Status.NO_ANSWER, 'Not Connected'))

    def test_the_switch_can_turn_it_off(self):
        row = self.call('a')
        with override_settings(ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False):
            self.end(row, 0)
        self.assertEqual(row.status, CallLog.Status.COMPLETED)

    # ---- past calls
    def stored(self, sid, duration, cost=0):
        row = self.call(sid, status=CallLog.Status.COMPLETED, age=3600)
        CallLog.objects.filter(pk=row.pk).update(duration=duration, platform_cost=cost)
        row.refresh_from_db()
        return row

    def fix(self, *flags):
        # The rows were made an hour ago, so yesterday..today (Eastern) holds them.
        today = timezone.now().astimezone(ZoneInfo('America/New_York')).date()
        out = StringIO()
        call_command('fix_zero_second_answers', '--start', str(today - timedelta(days=1)),
                     '--end', str(today), *flags, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        row = self.stored('a', 0)
        out = self.fix()
        self.assertIn('1 answered calls with 0 seconds', out)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.COMPLETED)

    def test_apply_marks_past_zero_second_calls(self):
        zero = self.stored('a', 0)
        talk = self.stored('b', 30)
        self.fix('--apply')
        zero.refresh_from_db()
        talk.refresh_from_db()
        self.assertEqual((zero.status, zero.block_reason), (CallLog.Status.NO_ANSWER, 'Not Connected'))
        self.assertEqual(talk.status, CallLog.Status.COMPLETED)
        rec = CallRecord.objects.get(id=zero.id)
        self.assertEqual(rec.status, CallRecord.Status.NO_ANSWER)
        self.assertFalse(rec.is_converted)

    def test_a_charged_call_is_left_alone(self):
        row = self.stored('a', 0, cost=0.05)
        out = self.fix('--apply')
        self.assertIn('CHARGED, left alone', out)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.COMPLETED)
