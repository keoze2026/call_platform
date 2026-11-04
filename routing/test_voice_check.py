"""CH-531: the voice check - pending list, results, staff-only visibility.

The listening itself (Silero VAD) runs on the host in
scripts/voice_check/voice_check.py and is not part of this test run.

Run:  python manage.py test routing.test_voice_check
"""
import io
import json
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from buyers.models import Buyer
from campaigns.models import Campaign
from routing.models import CallLog
from routing.signals import mirror_call_log


@override_settings(ALLOWED_HOSTS=['*'], VOICE_MIN_SECONDS=1.0,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class VoiceCheckTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-10', status='active')
        self.buyer = Buyer.objects.create(organization=self.org, name='Q11')
        self.n = 0

    def call(self, ended_min_ago=30, url='https://rec.example.com/recordings/{id}.mp3', **kw):
        self.n += 1
        ended = timezone.now() - timedelta(minutes=ended_min_ago)
        c = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyer,
                                   caller_number=f'+1555{self.n:07d}', called_number='+18443754175',
                                   twilio_call_sid=f's{self.n}', status='completed', duration=40,
                                   destination_number='+18334094605', ended_at=ended, **kw)
        if url:
            CallLog.objects.filter(pk=c.pk).update(recording_url=url.format(id=c.id))
        mirror_call_log(c.id)
        return CallLog.objects.get(pk=c.pk)

    def pending(self, *args):
        out = io.StringIO()
        call_command('voice_pending', *args, stdout=out)
        return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]

    def results(self, rows):
        out = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO('\n'.join(json.dumps(r) for r in rows) + '\n')):
            call_command('voice_results', stdout=out)
        return out.getvalue()

    def test_pending_lists_only_finished_recorded_unchecked_calls(self):
        ready = self.call()
        self.call(ended_min_ago=2)                 # just ended: its file may still be written
        self.call(url='')                           # no recording (never answered)
        self.call(voice_label='voice')              # already checked
        self.call(retry_hidden=True)                # a grouped try
        items = self.pending()
        self.assertEqual([i['id'] for i in items], [str(ready.id)])
        self.assertEqual(items[0]['file'], f'{ready.id}.mp3')

    def test_results_label_and_reach_the_report_copy(self):
        talk, silent, gone = self.call(), self.call(), self.call()
        out = self.results([
            {'id': str(talk.id), 'speech': 12.4, 'audio': 40.0},
            {'id': str(silent.id), 'speech': 0.3, 'audio': 40.0},
            {'id': str(gone.id), 'missing': True},
            {'id': 'not-a-call', 'speech': 1},
        ])
        self.assertIn('voice 1 | no_voice 1 | missing 1 | skipped 1', out)
        talk.refresh_from_db(); silent.refresh_from_db()
        self.assertEqual((talk.voice_label, talk.voice_seconds, talk.voice_audio_seconds), ('voice', 12.4, 40.0))
        self.assertEqual(silent.voice_label, 'no_voice')
        self.assertIsNotNone(silent.voice_checked_at)
        self.assertEqual(CallRecord.objects.get(id=silent.id).voice_label, 'no_voice')
        self.assertEqual(self.pending(), [])
        self.assertEqual([i['id'] for i in self.pending('--retry-missing')], [str(gone.id)])

    def api(self, role, buyer=None):
        u = User.objects.create(username=f'{role}{self.n}', email=f'{role}{self.n}@x.io', organization=self.org,
                                role=role, buyer=buyer)
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')

    def call_log(self, client, extra=''):
        day = timezone.now().date().isoformat()
        r = client.get(f'/api/analytics/calls?date_from={day}&date_to={day}&timezone=UTC{extra}')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['items']

    def test_staff_see_it_partners_never(self):
        silent = self.call(ended_min_ago=30)
        self.results([{'id': str(silent.id), 'speech': 0.0, 'audio': 40.0}])
        staff = self.call_log(self.api('admin'))
        self.assertEqual((staff[0]['voice_label'], staff[0]['voice_seconds']), ('no_voice', 0.0))
        buyer_rows = self.call_log(self.api('buyer', buyer=self.buyer))
        self.assertTrue(buyer_rows)
        self.assertEqual((buyer_rows[0]['voice_label'], buyer_rows[0]['voice_seconds']), (None, None))

    def test_staff_filter_and_report(self):
        talk, silent = self.call(), self.call()
        self.call()                                                    # not checked yet
        self.results([{'id': str(talk.id), 'speech': 9, 'audio': 40},
                      {'id': str(silent.id), 'speech': 0, 'audio': 40}])
        admin = self.api('admin')
        self.assertEqual([r['id'] for r in self.call_log(admin, '&voice=no_voice')], [str(silent.id)])
        self.assertEqual(len(self.call_log(admin, '&voice=unchecked')), 1)
        # A partner's filter is ignored: it neither narrows nor reveals anything.
        self.assertEqual(len(self.call_log(self.api('buyer', buyer=self.buyer), '&voice=no_voice')), 3)
        out = io.StringIO()
        day = timezone.localtime(timezone.now(), timezone=__import__('zoneinfo').ZoneInfo('America/New_York'))
        call_command('voice_report', '--date', f'{day:%Y-%m-%d}', stdout=out)
        self.assertIn('voice 1 | no voice 1 | missing 0 | not checked yet 1', out.getvalue())
        self.assertIn(str(silent.id), out.getvalue())
