"""CH-290: calls Completed on Revo but missed on ours because the answer
crossed the caller hanging up are corrected from Revo's call log.

Run:  python manage.py test routing.test_revo_sync
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import override_settings

from routing import revo_sync
from routing.models import CallLog
from routing.test_call_cleanup import Base

ENV = {'REVO_API_KEY': '541470|test', 'REVO_WORKSPACE': 'ws1'}


def fake_cfg(name, default=None, cast=str):
    value = ENV.get(name, default)
    return cast(value) if value is not None else value


class Resp:
    def __init__(self, rows, status=200, last_page=1):
        self.status_code = status
        self._body = {'success': True, 'message': '',
                      'payload': {'data': rows, 'current_page': 1, 'last_page': last_page}}

    def json(self):
        return self._body


class RevoSyncTests(Base):
    def setUp(self):
        super().setUp()
        for target, value in (('tasks.retire_used_number', None),
                              ('routing.revo_sync._cfg', fake_cfg)):
            p = mock.patch(target, value) if value else mock.patch(target)
            p.start()
            self.addCleanup(p.stop)
        self.campaign.revenue_amount = Decimal('1.00')
        self.campaign.min_call_duration = 0
        self.campaign.save()

    # 18704239446, 26 Oct: ours 1:29:08 rang 16s, caller hung up; Revo row
    # 5s later, answered after 12s, talked 1s.
    def ours(self, sid='a', caller='+18704239446', age=120, rang=16,
             status=CallLog.Status.NO_ANSWER, reason='Not Connected'):
        row = self.call(sid, caller=caller, age=age, status=status, destination='+18883413566')
        CallLog.objects.filter(pk=row.pk).update(
            block_reason=reason, ended_at=row.created_at + timedelta(seconds=rang))
        row.refresh_from_db()
        return row

    def revo(self, row, rid=22852950, status='Completed - With Conversion',
             duration=1, ttc=12, after=5, live=0, caller=None):
        t = row.created_at + timedelta(seconds=after)
        return {'id': rid, 'created_at': t.strftime('%Y-%m-%d %H:%M:%S'),
                'caller_number': caller or row.caller_number.lstrip('+'),
                'called_number': '18883413566', 'status': {'id': 1, 'name': status},
                'duration': duration, 'ttc': ttc, 'live': live}

    def run_cmd(self, rows, *flags):
        out = StringIO()
        session = mock.Mock()
        session.get.return_value = Resp(rows)
        with mock.patch('requests.Session', return_value=session):
            call_command('sync_revo', *flags, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        row = self.ours()
        out = self.run_cmd([self.revo(row)])
        self.assertIn('would fix', out)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_the_crossing_is_completed_paid_and_tagged(self):
        row = self.ours()
        out = self.run_cmd([self.revo(row)], '--apply')
        self.assertIn('1 fixed', out)
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration, row.block_reason),
                         (CallLog.Status.COMPLETED, 1, ''))
        self.assertEqual(row.revenue, Decimal('1.00'))
        self.assertIn('Completed by Revo', row.tags)
        self.assertIn('revo:22852950', row.tags)
        self.assertLessEqual(row.answered_at, row.ended_at)

    def test_running_again_changes_nothing(self):
        row = self.ours()
        self.run_cmd([self.revo(row)], '--apply')
        out = self.run_cmd([self.revo(row)], '--apply')
        self.assertIn('1 done', out)
        self.assertNotIn('fixed', out)

    def test_both_not_connected_is_left_alone(self):
        row = self.ours(rang=9)
        out = self.run_cmd([self.revo(row, status='Call Not Connected - With Conversion',
                                      duration=0, ttc=4)], '--apply')
        self.assertIn('1 ok', out)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_a_call_we_saw_answered_is_never_changed(self):
        row = self.ours(status=CallLog.Status.COMPLETED, reason='')
        CallLog.objects.filter(pk=row.pk).update(duration=95)
        out = self.run_cmd([self.revo(row, status='Call Not Connected', duration=0)], '--apply')
        self.assertIn('check', out)
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration), (CallLog.Status.COMPLETED, 95))

    def test_answer_far_from_our_hang_up_is_only_listed(self):
        row = self.ours(rang=16)
        out = self.run_cmd([self.revo(row, ttc=30)], '--apply')   # answered 19s after we hung up
        self.assertIn('check', out)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_a_long_revo_call_is_only_listed(self):
        row = self.ours()
        out = self.run_cmd([self.revo(row, duration=240)], '--apply')
        self.assertIn('check', out)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_a_refused_call_is_not_a_crossing(self):
        row = self.ours(reason='No Answer')
        out = self.run_cmd([self.revo(row)], '--apply')
        self.assertIn('check', out)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_another_caller_is_not_touched(self):
        row = self.ours()
        out = self.run_cmd([self.revo(row, caller='15550001111')], '--apply')
        self.assertIn('1 none', out)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_revo_errors_stop_without_changes(self):
        from django.core.management.base import CommandError
        session = mock.Mock()
        session.get.return_value = Resp([], status=401)
        with mock.patch('requests.Session', return_value=session):
            with self.assertRaises(CommandError):
                call_command('sync_revo', '--apply', stdout=StringIO())

    def test_revo_time_is_read_as_utc(self):
        rc = revo_sync.RevoCall.from_row({'id': 1, 'created_at': '2026-10-26 17:29:13',
                                          'status': {'name': 'Completed'}, 'duration': 1, 'ttc': 12})
        self.assertEqual(rc.started.isoformat(), '2026-10-26T17:29:13+00:00')
        self.assertEqual(rc.answered_at.isoformat(), '2026-10-26T17:29:25+00:00')
