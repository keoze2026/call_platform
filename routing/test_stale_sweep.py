"""CH-178: the stale sweep no longer closes calls that are still talking.

A buyer's 1h40m call vanished from Live and was written down as no answer,
because the sweep closed every call older than 60 minutes while Asterisk still
had it up.

Run:  python manage.py test routing.test_stale_sweep
"""
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from routing.models import CallLog
from routing.test_call_cleanup import Base, ENDED

HOUR_AND_A_BIT = 61 * 60


@override_settings(STALE_CALL_MINUTES=60, STALE_CALL_MINUTES_NO_SYNC=240,
                   BLIND_HANGUP_ASSUMES_ANSWERED=True)
class StaleSweepTests(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        p.start()
        self.addCleanup(p.stop)

    def sweep(self):
        from tasks import close_stale_calls
        return close_stale_calls()

    def status(self, row):
        row.refresh_from_db()
        return row.status

    def test_a_long_call_asterisk_still_has_stays_live(self):
        row = self.call('a', age=HOUR_AND_A_BIT)
        self.sync(['a'])
        self.sweep()
        self.assertEqual(self.status(row), CallLog.Status.IN_PROGRESS)

    def test_a_long_call_kept_by_its_caller_number_stays_live(self):
        # After a buyer transfer the channel id changes; the caller survives.
        row = self.call('a', age=HOUR_AND_A_BIT, caller='+15551234567')
        self.sync(['replaced-channel'], callers=['+15551234567'])
        self.sweep()
        self.assertEqual(self.status(row), CallLog.Status.IN_PROGRESS)

    def test_an_orphan_is_still_closed_while_the_sync_runs(self):
        live = self.call('a', age=HOUR_AND_A_BIT, caller='+15551234567')
        orphan = self.call('b', age=HOUR_AND_A_BIT, caller='+15550000001')
        self.sync(['a'])  # one report: not enough for the sync to close b itself
        self.sweep()
        self.assertEqual(self.status(live), CallLog.Status.IN_PROGRESS)
        self.assertEqual(self.status(orphan), CallLog.Status.NO_ANSWER)
        self.assertEqual(orphan.block_reason, 'stale_timeout')

    def test_without_the_sync_an_hour_old_call_is_left_alone(self):
        row = self.call('a', age=HOUR_AND_A_BIT)
        self.sweep()
        self.assertEqual(self.status(row), CallLog.Status.IN_PROGRESS)

    def test_without_the_sync_a_call_past_four_hours_is_closed(self):
        row = self.call('a', age=241 * 60)
        self.sweep()
        self.assertEqual(self.status(row), CallLog.Status.NO_ANSWER)

    def test_a_hangup_with_its_id_repairs_a_swept_call(self):
        row = self.call('a', age=2 * 3600, status=CallLog.Status.NO_ANSWER,
                        block_reason='stale_timeout')
        self.post(ENDED, {'call_log_id': str(row.id), 'duration': 5800,
                          'answered': True, 'recording_url': ''})
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration, row.block_reason),
                         (CallLog.Status.COMPLETED, 5800, ''))

    def test_a_hangup_without_its_id_still_finds_a_swept_call(self):
        row = self.call('a', age=2 * 3600, status=CallLog.Status.NO_ANSWER,
                        block_reason='stale_timeout')
        CallLog.objects.filter(pk=row.pk).update(ended_at=timezone.now() - timedelta(minutes=50))
        self.post(ENDED, {'call_log_id': '', 'duration': 0, 'answered': False,
                          'recording_url': '', 'caller': '+15551234567',
                          'dialed_seconds': 0, 'dial_status': ''})
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.COMPLETED)
        self.assertEqual(row.block_reason, '')
        self.assertGreater(row.duration, 3600)
