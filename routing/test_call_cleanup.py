"""CH-138: the channel sync no longer closes live calls, and a hangup that lost
its call id after a buyer transfer is finished as the answered call it was.

Run:  python manage.py test routing.test_call_cleanup
"""
import json
import uuid
from datetime import timedelta
from unittest import mock

from django.test import TestCase, Client, override_settings
from django.utils import timezone

from accounts.models import Organization
from campaigns.models import Campaign
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
SECRET = 'test-secret'
SYNC = '/api/twilio/asterisk/active-channels/'
ENDED = '/api/twilio/asterisk/call-ended/'


@override_settings(
    ASTERISK_SHARED_SECRET=SECRET,
    CACHES=CACHE,
    ASTERISK_SYNC_STRIKES=2,
    ASTERISK_SYNC_GRACE_SECONDS=120,
    ALLOWED_HOSTS=['*'],
    FRONTEND_URL='https://avortyx.test',
)
class Base(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.org = Organization.objects.create(name='Org')
        self.campaign = Campaign.objects.create(organization=self.org, name='C')
        self.client = Client()

    def call(self, sid, caller='+15551234567', age=600, status=CallLog.Status.IN_PROGRESS,
             destination='+15557654321', **extra):
        row = CallLog.objects.create(
            organization=self.org, campaign=self.campaign, caller_number=caller,
            called_number='+15559990000', twilio_call_sid=sid, status=status,
            destination_number=destination, **extra,
        )
        CallLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(seconds=age))
        row.refresh_from_db()
        return row

    def post(self, url, payload):
        return self.client.post(
            url, data=json.dumps(payload), content_type='application/json',
            HTTP_X_ASTERISK_SECRET=SECRET,
        )

    def sync(self, ids, callers=(), count=None):
        return self.post(SYNC, {
            'active_call_ids': list(ids),
            'active_callers': list(callers),
            'active_count': len(ids) if count is None else count,
        }).json()


class ChannelSyncTests(Base):
    def test_call_with_replaced_channel_is_kept_while_its_caller_is_on_a_channel(self):
        transferred = self.call('old-id', caller='+15551234567')
        other = self.call('other', caller='+15550001111')
        for _ in range(3):
            out = self.sync(['other', 'new-id'], callers=['15551234567', '5550001111'])
        transferred.refresh_from_db()
        self.assertEqual(transferred.status, CallLog.Status.IN_PROGRESS)
        self.assertEqual(out['closed'], 0)
        self.assertEqual(out['spared_by_caller'], 1)
        other.refresh_from_db()
        self.assertEqual(other.status, CallLog.Status.IN_PROGRESS)

    def test_one_missing_report_never_closes_a_call(self):
        gone = self.call('gone')
        self.call('still-here', caller='+15550002222')
        out = self.sync(['still-here'])
        gone.refresh_from_db()
        self.assertEqual(gone.status, CallLog.Status.IN_PROGRESS)
        self.assertEqual((out['closed'], out['waiting']), (0, 1))

    def test_second_missing_report_in_a_row_closes_it(self):
        gone = self.call('gone')
        self.call('still-here', caller='+15550002222')
        self.sync(['still-here'])
        out = self.sync(['still-here'])
        gone.refresh_from_db()
        self.assertEqual(gone.status, CallLog.Status.NO_ANSWER)
        self.assertEqual(gone.block_reason, 'asterisk_gone')
        self.assertEqual(out['closed'], 1)

    def test_reappearing_resets_the_count(self):
        row = self.call('flaky')
        self.call('anchor', caller='+15550002222')
        self.sync(['anchor'])                 # miss 1
        self.sync(['anchor', 'flaky'])        # back: count cleared
        self.sync(['anchor'])                 # miss 1 again, not 2
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.IN_PROGRESS)

    def test_zero_channels_still_needs_two_reports(self):
        row = self.call('x')
        self.sync([], count=0)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.IN_PROGRESS)
        self.sync([], count=0)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_unrelated_ids_close_nothing(self):
        row = self.call('x')
        for _ in range(3):
            out = self.sync(['some-other-format'])
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.IN_PROGRESS)
        self.assertEqual(out['closed'], 0)

    def test_young_rows_are_never_touched(self):
        row = self.call('young', age=30)
        for _ in range(3):
            self.sync([], count=0)
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.IN_PROGRESS)

    def test_short_or_withheld_caller_ids_never_protect_a_row(self):
        row = self.call('x', caller='+15551234567')
        self.call('anchor', caller='+15550002222')
        for _ in range(2):
            self.sync(['anchor'], callers=['100', 'anonymous'])
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_unreachable_cache_closes_nothing(self):
        row = self.call('x')
        self.call('anchor', caller='+15550002222')
        broken = mock.Mock()
        broken.add.side_effect = RuntimeError('redis down')
        with mock.patch('routing.asterisk_handler.cache', broken):
            for _ in range(3):
                self.sync(['anchor'])
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.IN_PROGRESS)

    def test_a_call_finished_meanwhile_keeps_its_outcome(self):
        row = self.call('x')
        self.call('anchor', caller='+15550002222')
        self.sync(['anchor'])
        CallLog.objects.filter(pk=row.pk).update(status=CallLog.Status.COMPLETED)
        self.sync(['anchor'])
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.COMPLETED)

    def test_older_script_without_callers_still_works(self):
        row = self.call('x')
        self.call('anchor', caller='+15550002222')
        for _ in range(2):
            self.post(SYNC, {'active_call_ids': ['anchor'], 'active_count': 1})
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)


@override_settings(BLIND_HANGUP_ASSUMES_ANSWERED=True)
class HangupReportTests(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        p.start()
        self.addCleanup(p.stop)

    def blind(self, **kw):
        payload = {
            'call_log_id': '', 'duration': 0, 'answered': False,
            'recording_url': 'recordings/.wav', 'caller': '+15551234567',
            'dialed_seconds': 0, 'dial_status': '',
        }
        payload.update(kw)
        return self.post(ENDED, payload)

    def test_normal_report_is_unchanged(self):
        row = self.call('a')
        r = self.post(ENDED, {'call_log_id': str(row.id), 'duration': 61, 'answered': True,
                              'dialed_seconds': 70, 'dial_status': 'ANSWER'})
        row.refresh_from_db()
        self.assertEqual(r.status_code, 200)
        self.assertEqual((row.status, row.duration, row.ring_seconds),
                         (CallLog.Status.COMPLETED, 61, 9))

    def test_garbled_call_id_is_not_found_not_a_server_error(self):
        r = self.post(ENDED, {'call_log_id': 'not-a-uuid', 'duration': 5, 'answered': True})
        self.assertEqual(r.status_code, 404)
        r = self.post(ENDED, {'call_log_id': str(uuid.uuid4()), 'duration': 5, 'answered': True})
        self.assertEqual(r.status_code, 404)

    def test_replayed_report_keeps_the_time_the_call_really_ended(self):
        row = self.call('a')
        ended = int((timezone.now() - timedelta(minutes=30)).timestamp())
        self.post(ENDED, {'call_log_id': str(row.id), 'duration': 120, 'answered': True,
                          'ended_at_epoch': ended})
        row.refresh_from_db()
        self.assertEqual(int(row.ended_at.timestamp()), ended)
        self.assertEqual(int(row.answered_at.timestamp()), ended - 120)

    def test_implausible_end_times_are_ignored(self):
        for epoch in (int((timezone.now() + timedelta(days=1)).timestamp()), 1000):
            row = self.call(f'a{epoch}')
            self.post(ENDED, {'call_log_id': str(row.id), 'duration': 5, 'answered': True,
                              'ended_at_epoch': epoch})
            row.refresh_from_db()
            self.assertLess(abs((row.ended_at - timezone.now()).total_seconds()), 30)

    def test_hangup_without_id_finishes_the_transferred_call_as_answered(self):
        row = self.call('t', age=600)
        r = self.blind()
        row.refresh_from_db()
        self.assertTrue(r.json().get('received'))
        self.assertEqual(row.status, CallLog.Status.COMPLETED)
        self.assertGreaterEqual(row.duration, 595)
        self.assertEqual(row.recording_url, f'https://avortyx.test/recordings/{row.id}.wav')

    def test_blind_hangup_can_be_left_as_reported(self):
        row = self.call('t', age=600)
        with override_settings(BLIND_HANGUP_ASSUMES_ANSWERED=False):
            self.blind()
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration), (CallLog.Status.NO_ANSWER, 0))

    def test_blind_hangup_with_a_dial_result_is_a_real_missed_call(self):
        row = self.call('t', age=600)
        self.blind(dial_status='NOANSWER', dialed_seconds=30)
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration), (CallLog.Status.NO_ANSWER, 0))

    def test_blind_hangup_for_a_call_never_sent_to_a_buyer_is_not_assumed_answered(self):
        row = self.call('t', age=600, destination='')
        self.blind()
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)

    def test_blind_hangup_prefers_the_call_sent_to_a_buyer(self):
        talking = self.call('talking', age=900)
        ringing = self.call('ringing', age=20, status=CallLog.Status.RINGING, destination='')
        self.blind()
        talking.refresh_from_db()
        ringing.refresh_from_db()
        self.assertEqual(talking.status, CallLog.Status.COMPLETED)
        self.assertEqual(ringing.status, CallLog.Status.RINGING)

    def test_hangup_that_loses_the_race_to_the_sweep_still_finishes_the_call(self):
        row = self.call('t', age=600, status=CallLog.Status.NO_ANSWER, block_reason='asterisk_gone')
        CallLog.objects.filter(pk=row.pk).update(ended_at=timezone.now() - timedelta(minutes=2))
        self.blind()
        row.refresh_from_db()
        self.assertEqual(row.status, CallLog.Status.COMPLETED)
        self.assertEqual(row.block_reason, '')

    def test_blind_hangup_with_nothing_open_changes_nothing(self):
        done = self.call('d', status=CallLog.Status.COMPLETED, duration=40)
        r = self.blind()
        done.refresh_from_db()
        self.assertEqual(r.json(), {'received': True, 'matched': False})
        self.assertEqual((done.status, done.duration), (CallLog.Status.COMPLETED, 40))

    def test_campaign_with_recording_off_gets_no_rebuilt_link(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(recording_enabled=False)
        row = self.call('t', age=600)
        self.blind()
        row.refresh_from_db()
        self.assertEqual(row.recording_url, '')


class UnansweredRingTimeTests(Base):
    """TTC of a call nobody answered (CH-139)."""

    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        p.start()
        self.addCleanup(p.stop)

    def end(self, row, **kw):
        payload = {'call_log_id': str(row.id), 'duration': 0, 'answered': False,
                   'dialed_seconds': 0, 'dial_status': 'CANCEL'}
        payload.update(kw)
        self.post(ENDED, payload)
        row.refresh_from_db()

    def test_caller_who_gives_up_is_charged_the_wait_not_zero(self):
        row = self.call('a', age=44)
        self.end(row)
        self.assertEqual(row.block_reason, 'Not Connected')
        self.assertTrue(43 <= row.ring_seconds <= 47, row.ring_seconds)

    def test_a_reported_dial_time_wins_when_the_switch_sends_one(self):
        row = self.call('a', age=44)
        self.end(row, dial_status='NOANSWER', dialed_seconds=30)
        self.assertEqual(row.ring_seconds, 30)

    def test_instant_decline_is_about_zero(self):
        row = self.call('a', age=1)
        self.end(row, dial_status='CONGESTION')
        self.assertEqual(row.block_reason, 'No Destination')
        self.assertLessEqual(row.ring_seconds, 3)

    def test_call_that_never_dialed_stays_unknown(self):
        row = self.call('a', age=44)
        self.end(row, dial_status='')
        self.assertIsNone(row.ring_seconds)

    def test_answered_call_ring_time_is_unchanged(self):
        row = self.call('a', age=90)
        self.end(row, duration=61, answered=True, dialed_seconds=70, dial_status='ANSWER')
        self.assertEqual(row.ring_seconds, 9)


class NumberClockTests(Base):
    """A number is used when a buyer answered a call on it (CH-143)."""

    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        self.queued = p.start()
        self.addCleanup(p.stop)

    def end(self, row, **kw):
        payload = {'call_log_id': str(row.id), 'duration': 0, 'answered': False,
                   'dialed_seconds': 0, 'dial_status': 'CANCEL'}
        payload.update(kw)
        self.post(ENDED, payload)

    def test_an_answered_call_starts_the_clock(self):
        row = self.call('a', age=90)
        self.end(row, duration=61, answered=True, dialed_seconds=70, dial_status='ANSWER')
        self.queued.delay.assert_called_once_with(row.called_number, str(row.id))

    def test_a_call_the_caller_hung_up_on_does_not(self):
        row = self.call('a', age=44)
        self.end(row)
        self.queued.delay.assert_not_called()

    def test_a_buyer_who_did_not_answer_does_not(self):
        row = self.call('a', age=30)
        self.end(row, dial_status='NOANSWER', dialed_seconds=30)
        self.queued.delay.assert_not_called()

    def test_a_call_refused_before_dialing_does_not(self):
        row = self.call('a', age=5, destination='')
        self.end(row, dial_status='')
        self.queued.delay.assert_not_called()


class NumberClockLifecycleTests(TestCase):
    """What the clock does to the number once an answered call queues it."""

    def setUp(self):
        from phone_numbers.models import Carrier, PhoneNumber
        self.org = Organization.objects.create(name='Org2')
        self.kmq = Carrier.objects.create(
            organization=self.org, name='Freedom', code='KMQ', lifetime_hours=24)
        self.number = PhoneNumber.objects.create(
            organization=self.org, number='+18774893792', carrier=self.kmq, vendor='other')

    def test_first_answered_use_stamps_the_24_hours(self):
        from phone_numbers.lifecycle import retire_if_single_use
        retire_if_single_use('+18774893792', 'call-1')
        self.number.refresh_from_db()
        self.assertIsNotNone(self.number.used_at)
        self.assertAlmostEqual(
            (self.number.expires_at - self.number.used_at).total_seconds(), 24 * 3600, delta=2)

    def test_an_unused_number_has_no_clock(self):
        self.number.refresh_from_db()
        self.assertIsNone(self.number.used_at)
        self.assertIsNone(self.number.expires_at)


class NotConnectedLabelTests(Base):
    """Every call is judged on its own: a caller who was missed before is not
    marked missed for good (Sir, 20 Oct: keep trying, no hard rule)."""

    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        p.start()
        self.addCleanup(p.stop)
        from buyers.models import Buyer
        self.buyer = Buyer.objects.create(organization=self.org, name='ADST')

    def ring(self, sid, status=CallLog.Status.NO_ANSWER, age=600, **kw):
        return self.call(sid, caller='16283585660', age=age, status=status,
                         buyer=self.buyer, **kw)

    def end(self, row, dial_status):
        self.post(ENDED, {'call_log_id': str(row.id), 'duration': 0, 'answered': False,
                          'dialed_seconds': 0, 'dial_status': dial_status})
        row.refresh_from_db()
        return row.block_reason

    def test_a_caller_who_hangs_up_is_not_connected(self):
        row = self.ring('now', status=CallLog.Status.IN_PROGRESS, age=24)
        self.assertEqual(self.end(row, 'CANCEL'), 'Not Connected')

    def test_earlier_misses_do_not_change_this_call(self):
        self.ring('before-1', age=120)
        self.ring('before-2', age=60)
        row = self.ring('now', status=CallLog.Status.IN_PROGRESS, age=24)
        self.assertEqual(self.end(row, 'CANCEL'), 'Not Connected')

    def test_a_line_that_could_not_take_the_call_reads_no_destination(self):
        for status in ('CONGESTION', 'CHANUNAVAIL'):
            row = self.ring('l-' + status, status=CallLog.Status.IN_PROGRESS, age=10)
            self.assertEqual(self.end(row, status), 'No Destination')

    def test_busy_and_no_answer_wording_is_unchanged(self):
        row = self.ring('a', status=CallLog.Status.IN_PROGRESS, age=10)
        self.assertEqual(self.end(row, 'BUSY'), 'Destination busy')

    def test_the_rows_already_written_are_renamed(self):
        from importlib import import_module
        from django.apps import apps
        old = self.ring('old', age=300, block_reason='Caller Abandoned')
        import_module('routing.migrations.0015_not_connected_label').rename(
            'Caller Abandoned', 'Not Connected')(apps, None)
        old.refresh_from_db()
        self.assertEqual(old.block_reason, 'Not Connected')


class MissedCallRecordingTests(Base):
    """A call nobody answered has no recording link (Sir, 20 Oct)."""

    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        p.start()
        self.addCleanup(p.stop)

    def end(self, row, **kw):
        payload = {'call_log_id': str(row.id), 'duration': 0, 'answered': False,
                   'dialed_seconds': 0, 'dial_status': 'CANCEL',
                   'recording_url': f'https://avortyx.test/recordings/{row.id}.wav'}
        payload.update(kw)
        self.post(ENDED, payload)
        row.refresh_from_db()
        return row

    def test_a_missed_call_stores_no_recording_link(self):
        row = self.end(self.call('a'))
        self.assertEqual(row.status, CallLog.Status.NO_ANSWER)
        self.assertEqual(row.recording_url, '')

    def test_a_rejected_call_stores_no_recording_link(self):
        row = self.end(self.call('a'), dial_status='CONGESTION', dialed_seconds=0)
        self.assertEqual(row.recording_url, '')

    def test_an_answered_call_keeps_its_recording_link(self):
        row = self.end(self.call('a'), answered=True, duration=61, dialed_seconds=70,
                       dial_status='ANSWER')
        self.assertEqual(row.status, CallLog.Status.COMPLETED)
        self.assertEqual(row.recording_url, f'https://avortyx.test/recordings/{row.id}.wav')

    def test_a_blind_hangup_left_unanswered_gets_no_rebuilt_link(self):
        row = self.call('t', age=600)
        with override_settings(BLIND_HANGUP_ASSUMES_ANSWERED=False):
            self.post(ENDED, {'call_log_id': '', 'duration': 0, 'answered': False,
                              'recording_url': 'recordings/.wav', 'caller': '+15551234567',
                              'dialed_seconds': 0, 'dial_status': ''})
        row.refresh_from_db()
        self.assertEqual(row.recording_url, '')

    def test_the_cleanup_clears_missed_calls_and_their_mirror_only(self):
        import importlib
        from django.apps import apps as live_apps
        from analytics.models import CallRecord
        mod = importlib.import_module('routing.migrations.0016_clear_missed_recordings')
        url = 'https://avortyx.test/recordings/x.wav'
        missed = self.call('m', status=CallLog.Status.NO_ANSWER, recording_url=url)
        failed = self.call('f', status=CallLog.Status.FAILED, recording_url=url)
        done = self.call('d', status=CallLog.Status.COMPLETED, recording_url=url, duration=30)
        for row in (missed, failed, done):
            CallRecord.objects.filter(pk=row.pk).update(recording_url=url)
        mod.clear_missed_recordings(live_apps, None)
        for row, expected in ((missed, ''), (failed, ''), (done, url)):
            row.refresh_from_db()
            self.assertEqual(row.recording_url, expected)
            rec = CallRecord.objects.filter(pk=row.pk).first()
            if rec is not None:
                self.assertEqual(rec.recording_url, expected)

    def test_repair_never_puts_a_link_back_on_a_missed_call(self):
        import tempfile
        from django.core.management import call_command
        from io import StringIO
        missed = self.call('m', status=CallLog.Status.NO_ANSWER)
        done = self.call('d', status=CallLog.Status.COMPLETED, duration=30)
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as fh:
            fh.write(f'{missed.id}.wav\n{done.id}.wav\n')
        call_command('repair_recordings', from_list=fh.name, stdout=StringIO())
        missed.refresh_from_db()
        done.refresh_from_db()
        self.assertEqual(missed.recording_url, '')
        self.assertTrue(done.recording_url.endswith(f'{done.id}.wav'))
