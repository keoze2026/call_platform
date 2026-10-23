"""CH-187: an answer with 0 seconds of talk is Not Connected, as in Revo.

Run:  python manage.py test routing.test_zero_second_answer
"""
from datetime import timedelta, timezone as dt_timezone
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

    # ---- CH-222: switched off, an answer under a second reads as Revo shows it
    def test_switched_off_under_a_second_is_completed_one_second(self):
        row = self.call('a')
        with override_settings(ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False):
            self.end(row, 0)
        self.assertEqual((row.status, row.duration, row.block_reason, row.ring_seconds),
                         (CallLog.Status.COMPLETED, 1, '', 9))

    def test_switched_off_a_real_second_keeps_its_ring(self):
        row = self.call('a')
        with override_settings(ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False):
            self.end(row, 1)
        self.assertEqual((row.status, row.duration, row.ring_seconds),
                         (CallLog.Status.COMPLETED, 1, 8))

    def test_switched_off_an_unanswered_call_is_still_missed(self):
        row = self.call('a')
        with override_settings(ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False):
            self.end(row, 0, answered=False, dial_status='CANCEL')
        self.assertEqual((row.status, row.block_reason), (CallLog.Status.NO_ANSWER, 'Not Connected'))

    # ---- CH-222: restore past calls with the CDR as proof
    def cdr_row(self, row, disposition, billsec, rang=10):
        start = row.created_at.astimezone(dt_timezone.utc).replace(microsecond=0)
        fmt = lambda t: t.strftime('%Y-%m-%d %H:%M:%S')
        answer = fmt(start + timedelta(seconds=rang)) if disposition == 'ANSWERED' else ''
        return ['', row.caller_number, '18552026099', 'from-carrier', '""', 'PJSIP/carrier-1',
                'PJSIP/carrier-2', 'Dial', f'PJSIP/{row.destination_number}@carrier,3600,gr',
                fmt(start), answer, fmt(start + timedelta(seconds=rang + 1)), str(rang + 1),
                str(billsec), disposition, 'DOCUMENTATION', '1.1', '']

    def restore(self, rows, *flags):
        import csv, tempfile
        today = timezone.now().astimezone(ZoneInfo('America/New_York')).date()
        with tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, newline='') as fh:
            csv.writer(fh, quoting=csv.QUOTE_ALL).writerows(rows)
        out = StringIO()
        call_command('restore_short_answers', '--start', str(today - timedelta(days=1)),
                     '--end', str(today), '--cdr', fh.name, *flags, stdout=out)
        return out.getvalue()

    def missed_row(self, sid, caller):
        row = self.call(sid, caller=caller, age=600, status=CallLog.Status.NO_ANSWER)
        CallLog.objects.filter(pk=row.pk).update(block_reason='Not Connected', duration=0)
        row.refresh_from_db()
        return row

    def test_restore_puts_back_only_calls_the_switch_answered(self):
        short = self.missed_row('a', '+19739700901')
        rang_out = self.missed_row('b', '+19368279603')
        rows = [self.cdr_row(short, 'ANSWERED', 0), self.cdr_row(rang_out, 'NO ANSWER', 0, rang=8)]
        dry = self.restore(rows)
        self.assertIn('1 of them are Not Connected', dry)
        short.refresh_from_db()
        self.assertEqual(short.status, CallLog.Status.NO_ANSWER)
        self.restore(rows, '--apply')
        short.refresh_from_db()
        rang_out.refresh_from_db()
        self.assertEqual((short.status, short.duration, short.block_reason, short.ring_seconds),
                         (CallLog.Status.COMPLETED, 1, '', 10))
        self.assertEqual(CallRecord.objects.get(id=short.id).status, CallRecord.Status.COMPLETED)
        self.assertEqual((rang_out.status, rang_out.block_reason),
                         (CallLog.Status.NO_ANSWER, 'Not Connected'))

    def test_restore_leaves_a_real_answered_call_alone(self):
        talk = self.missed_row('a', '+15550001111')
        self.restore([self.cdr_row(talk, 'ANSWERED', 42)], '--apply')
        talk.refresh_from_db()
        self.assertEqual(talk.status, CallLog.Status.NO_ANSWER)

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
