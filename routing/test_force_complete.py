"""CH-226: a call Revo shows completed is forced Completed on Sir's word.

Run:  python manage.py test routing.test_force_complete
"""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from routing.models import CallLog
from routing.test_call_cleanup import Base


class ForceCompleteTests(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        self.retire = p.start()
        self.addCleanup(p.stop)
        self.campaign.revenue_amount = Decimal('1.00')
        self.campaign.min_call_duration = 0
        self.campaign.save()

    def missed(self, sid, caller='+18032426735', age=120, **extra):
        row = self.call(sid, caller=caller, age=age, status=CallLog.Status.NO_ANSWER, **extra)
        CallLog.objects.filter(pk=row.pk).update(block_reason='Not Connected', ring_seconds=15)
        row.refresh_from_db()
        return row

    def force(self, caller, *flags):
        out = StringIO()
        call_command('force_complete', '--caller', caller, *flags, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        row = self.missed('a')
        self.assertIn('[dry run]', self.force('18032426735:2'))
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_apply_completes_with_revos_duration_and_pays(self):
        row = self.missed('a')
        self.force('18032426735:2', '--apply')
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration, row.block_reason, row.ring_seconds),
                         (CallLog.Status.COMPLETED, 2, '', 15))
        self.assertEqual(row.revenue, Decimal('1.00'))
        self.retire.delay.assert_called_once()

    def test_several_callers_at_once_and_default_one_second(self):
        a = self.missed('a')
        b = self.missed('b', caller='+19368279603')
        self.force('18032426735:2, 19368279603', '--apply')
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.duration, b.duration), (2, 1))
        self.assertEqual(b.status, CallLog.Status.COMPLETED)

    def test_only_the_latest_missed_call_of_the_caller(self):
        old = self.missed('a', age=3000)
        new = self.missed('b', age=120)
        out = self.force('18032426735:2', '--apply')
        self.assertIn('not touched', out)
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(old.status, CallLog.Status.NO_ANSWER)
        self.assertEqual(new.status, CallLog.Status.COMPLETED)

    def test_other_callers_and_answered_calls_are_untouched(self):
        other = self.missed('a', caller='+15550001111')
        out = self.force('18032426735:2', '--apply')
        self.assertIn('nothing to change', out)
        other.refresh_from_db()
        self.assertEqual(other.status, CallLog.Status.NO_ANSWER)

    def test_a_hidden_try_is_never_the_one_changed(self):
        hidden = self.missed('a', retry_hidden=True)
        self.force('18032426735:2', '--apply')
        hidden.refresh_from_db()
        self.assertEqual(hidden.status, CallLog.Status.NO_ANSWER)

    def test_bad_input_is_refused(self):
        with self.assertRaises(CommandError):
            self.force('12345:2')
        with self.assertRaises(CommandError):
            self.force('18032426735:0')
