"""CH-661: every Completed call has a recording (Sir, 10 Nov).

10 Nov, DD-00: +15632100386 on Q04 was Completed, 1 second (the buyer
answered during the cancel hold, after the caller hung up) with no recording.
Since 9 Nov, 46 of 83 such calls had none: a call made Completed after its
hang-up kept no link. Each path that does so now keeps the call's own link.

Run:  python manage.py test routing.test_completed_recording
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from routing.models import CallLog
from routing.recordings import completed_recording_url
from routing.test_call_cleanup import Base


@override_settings(FRONTEND_URL='https://app.example.com', PAID_MISSED_RECORDING=False)
class CompletedRecordingTests(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        p.start()
        self.addCleanup(p.stop)
        self.campaign.revenue_amount = Decimal('1.00')
        self.campaign.min_call_duration = 0
        self.campaign.save()

    def missed(self, sid, caller='+15632100386'):
        row = self.call(sid, caller=caller, age=120, status=CallLog.Status.NO_ANSWER,
                        ended_at=timezone.now() - timedelta(seconds=60))
        CallLog.objects.filter(pk=row.pk).update(block_reason='Not Connected', recording_url='')
        row.refresh_from_db()
        return row

    def link(self, row):
        return f'https://app.example.com/recordings/{row.id}.wav'

    def test_an_answer_after_the_hang_up_gets_the_recording(self):
        # The cancel hold's report after the hang-up goes through mark() (CH-296).
        from routing.late_answer import mark
        row = self.missed('a')
        now = timezone.now().timestamp()
        self.assertTrue(mark(row, {'answer_at': now, 'cancel_at': now}))
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration, row.recording_url),
                         (CallLog.Status.COMPLETED, 1, self.link(row)))

    def test_force_complete_gets_the_recording(self):
        row = self.missed('b', caller='+18032426735')
        call_command('force_complete', '--caller', '18032426735:2', '--apply', stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual((row.status, row.recording_url), (CallLog.Status.COMPLETED, self.link(row)))

    def test_a_campaign_with_recording_off_gets_none(self):
        from routing.late_answer import mark
        self.campaign.recording_enabled = False
        self.campaign.save()
        row = self.missed('c')
        now = timezone.now().timestamp()
        mark(row, {'answer_at': now, 'cancel_at': now})
        row.refresh_from_db()
        self.assertEqual((row.status, row.recording_url), (CallLog.Status.COMPLETED, ''))

    def test_a_call_keeps_the_link_it_has(self):
        row = self.missed('d')
        row.recording_url = 'https://rec.example.com/recordings/x.mp3'
        self.assertEqual(completed_recording_url(row), 'https://rec.example.com/recordings/x.mp3')
