"""CH-281: calls point at the MP3 once their recording is converted."""
import io
import uuid
from datetime import datetime, timezone as tz

from django.core.management import call_command
from django.test import TestCase

from accounts.models import Organization
from analytics.models import CallRecord
from routing.models import CallLog


class RecordingsToMp3Tests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')

    def call(self, url):
        cid = uuid.uuid4()
        CallLog.objects.create(id=cid, organization=self.org, caller_number='+15550001111',
                               called_number='+18885550101', twilio_call_sid=str(cid),
                               status='completed', recording_url=url)
        CallRecord.objects.create(id=cid, organization=self.org, caller_number='+15550001111',
                                  status='completed', started_at=datetime.now(tz.utc), recording_url=url)
        return str(cid)

    def run_cmd(self, *args):
        out, err = io.StringIO(), io.StringIO()
        call_command('recordings_to_mp3', *args, stdout=out, stderr=err)
        return out.getvalue()

    def test_links_switch_to_mp3_in_call_log_and_reports(self):
        a = self.call('https://rec.v0l1.com/recordings/PLACEHOLDER.wav')
        CallLog.objects.filter(pk=a).update(recording_url=f'https://rec.v0l1.com/recordings/{a}.wav')
        CallRecord.objects.filter(pk=a).update(recording_url=f'https://rec.v0l1.com/recordings/{a}.wav')
        out = self.run_cmd(a)
        self.assertIn(f'ok {a} 2', out)
        self.assertEqual(CallLog.objects.get(pk=a).recording_url, f'https://rec.v0l1.com/recordings/{a}.mp3')
        self.assertEqual(CallRecord.objects.get(pk=a).recording_url, f'https://rec.v0l1.com/recordings/{a}.mp3')

    def test_other_calls_and_unknown_ids_are_left_alone(self):
        a = self.call('')
        CallLog.objects.filter(pk=a).update(recording_url=f'https://rec.v0l1.com/recordings/{a}.wav')
        b = self.call('https://rec.v0l1.com/recordings/other.wav')
        missing = str(uuid.uuid4())
        out = self.run_cmd(missing, a)
        self.assertIn(f'none {missing} 0', out)
        self.assertEqual(CallLog.objects.get(pk=b).recording_url, 'https://rec.v0l1.com/recordings/other.wav')

    def test_dry_run_changes_nothing(self):
        a = self.call('')
        CallLog.objects.filter(pk=a).update(recording_url=f'https://rec.v0l1.com/recordings/{a}.wav')
        out = self.run_cmd(a, '--dry-run')
        self.assertIn(f'ok {a} 1', out)
        self.assertTrue(CallLog.objects.get(pk=a).recording_url.endswith('.wav'))

    def test_not_a_call_id_is_refused(self):
        out = self.run_cmd("x' OR 1=1 --")
        self.assertEqual(out, '')
