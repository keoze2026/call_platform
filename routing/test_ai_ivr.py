"""CH-553: Voicemail / IVR - no speech, music (PANNs); re-check of calls
already labelled; the PANNs hook in voice_check.py.

4 Nov: three IVR calls (only music) came out "No Voice | Dead Caller".
On Server B, sound_check.py try heard: music 20 of 20 s, 60 of 62 s.

Run:  python manage.py test routing.test_ai_ivr
"""
import importlib.util
import io
import json
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from analytics.models import CallRecord
from campaigns.models import Campaign
from routing import ai_labels
from routing.models import CallLog
from routing.signals import mirror_call_log


def two(caller, agent, beep=0.0, **kw):
    return {'id': 'x', 'channels': 2, 'speech': caller + agent, 'audio': 60, 'caller_speech': caller,
            'agent_speech': agent, 'beep': beep, **kw}


@override_settings(VOICE_MIN_SECONDS=1.0, AI_BEEP_SECONDS=0.3, AI_MUSIC_SECONDS=4, AI_MUSIC_SHARE=0.4)
class IvrRuleTests(SimpleTestCase):
    def test_the_three_calls_from_4_nov(self):
        # one mixed recording (split files were not reachable), as measured on Server B
        for speech, music, heard in ((0.0, 20, 20), (0.0, 60, 62)):
            r = ai_labels.decide({'id': 'x', 'channels': 1, 'speech': speech, 'audio': heard,
                                  'music': music, 'sound_s': heard})
            self.assertEqual((r['label'], r['confidence']), ('ivr', 85))
            self.assertIn(f'music {music} s of {heard} s (one recording), no human speech', r['comment'])
        self.assertEqual(ai_labels.NAMES['ivr'], 'Voicemail / IVR')
        self.assertIn('ivr', ai_labels.LABELS)                      # the audit team can pick it
        self.assertNotIn('ivr', ai_labels.NO_VOICE)                 # not one of Sir's three No Voice types

    def test_without_panns_nothing_changes(self):
        self.assertEqual(ai_labels.decide({'id': 'x', 'speech': 0.0, 'audio': 30})['label'], 'dead_caller')
        self.assertEqual(ai_labels.decide(two(0, 0))['label'], 'dead_caller')
        self.assertEqual(ai_labels.decide(two(0, 5))['label'], 'agent_only')

    def test_a_little_music_is_not_enough(self):
        one = lambda m, s: ai_labels.decide({'id': 'x', 'speech': 0, 'music': m, 'sound_s': s})['label']
        self.assertEqual(one(2, 4), 'dead_caller')                   # under 4 s
        self.assertEqual(one(6, 60), 'dead_caller')                  # 10% of what was heard
        self.assertEqual(one(30, 60), 'ivr')
        r = ai_labels.decide({'id': 'x', 'speech': 0, 'music': 30, 'sound_s': 60})
        self.assertEqual(r['confidence'], 70)                         # half music: less sure

    def test_speech_wins_over_music(self):
        r = {'id': 'x', 'speech': 8.0, 'music': 40, 'sound_s': 60}
        self.assertEqual(ai_labels.decide(r)['label'], 'mixed')
        self.assertEqual(ai_labels.decide(two(5, 3, caller_music=40, caller_sound_s=60))['label'], 'valid')

    def test_two_channels(self):
        self.assertEqual(ai_labels.decide(two(0, 0, caller_music=50, caller_sound_s=60))['label'], 'ivr')
        r = ai_labels.decide(two(0, 0, agent_music=50, agent_sound_s=60))
        self.assertEqual(r['label'], 'ivr')
        self.assertIn("on the agent's side", r['comment'])
        # the agent talks over hold music: still the agent's opening, caller silent
        self.assertEqual(ai_labels.decide(two(0, 6, agent_music=50, agent_sound_s=60))['label'], 'agent_only')
        # PANNs' machine tones on the caller side count as beeping
        self.assertEqual(ai_labels.decide(two(0, 6, caller_tone=4, caller_music=0, caller_sound_s=60))['label'],
                         'agent_beep')

    def test_a_file_with_no_audio_is_still_a_fault(self):
        r = ai_labels.decide({'id': 'x', 'speech': 0, 'no_data': True, 'music': 30, 'sound_s': 30})
        self.assertEqual((r['label'], r['fault']), ('dead_caller', True))


@override_settings(VOICE_MIN_SECONDS=1.0, AI_MUSIC_SECONDS=4, AI_MUSIC_SHARE=0.4, VOICE_TELEGRAM_ALERTS=True,
                   REPORTS_BOT_TOKEN='t',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class RecheckTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='C-07', status='active')
        self.n = 0

    def call(self, label):
        self.n += 1
        c = CallLog.objects.create(organization=self.org, campaign=self.camp, caller_number=f'+1555{self.n:07d}',
                                   called_number='+18334973067', twilio_call_sid=f's{self.n}', status='completed',
                                   duration=21, voice_label='no_voice', ai_label=label,
                                   ai_checked_at=timezone.now(), ended_at=timezone.now() - timedelta(hours=1),
                                   recording_url=f'https://rec.example.com/recordings/s{self.n}.mp3')
        mirror_call_log(c.id)
        return c

    def test_dead_callers_listed_again_and_relabelled_without_a_new_alert(self):
        from analytics.models import TeamReportChat
        TeamReportChat.objects.create(organization=self.org, chat_id='-100', title='Team')
        dead, valid = self.call('dead_caller'), self.call('valid')
        out = io.StringIO()
        call_command('voice_pending', '--ai-label', 'dead_caller', stdout=out)
        self.assertEqual([json.loads(line)['id'] for line in out.getvalue().splitlines()], [str(dead.id)])
        line = json.dumps({'id': str(dead.id), 'channels': 1, 'speech': 0.0, 'audio': 21, 'music': 20,
                           'sound_s': 20}) + '\n'
        with mock.patch('sys.stdin', io.StringIO(line)), mock.patch('analytics.team_report.telegram') as tg:
            call_command('voice_results', stdout=io.StringIO())
        tg.assert_not_called()                                         # IVR is not a No Voice alert
        dead.refresh_from_db()
        self.assertEqual(dead.ai_label, 'ivr')
        self.assertEqual(CallRecord.objects.get(pk=dead.pk).ai_label, 'ivr')
        valid.refresh_from_db()
        self.assertEqual(valid.ai_label, 'valid')


class VoiceCheckHookTests(SimpleTestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location('voice_check', 'scripts/voice_check/voice_check.py')
        vc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vc)
        return vc

    def test_panns_is_asked_only_about_a_silent_side_and_its_numbers_are_sent(self):
        vc = self.load()
        sides = iter([{'speech': 0.0, 'beep': 0.0, 'first': None, 'no_data': False},
                      {'speech': 0.0, 'beep': 0.0, 'first': None, 'no_data': False}])
        vad = mock.Mock(side=lambda pcm: next(sides))
        with mock.patch.object(vc, 'fetch', side_effect=['/tmp/c.wav', '/tmp/a.wav']), \
                mock.patch.object(vc, 'decode', return_value=b'\0\0' * 16000), \
                mock.patch.object(vc.os, 'unlink'), \
                mock.patch.object(vc.os.path, 'isfile', return_value=False), \
                mock.patch.object(vc, 'sound_of', side_effect=[{'music': 18, 'tone': 0, 'speech': 0, 'seconds': 20},
                                                              {'music': 0, 'tone': 0, 'speech': 0, 'seconds': 20}]) as snd:
            r = vc.split_sides(vad, {'id': 'x', 'talk': 20, 'split': {'caller': 'https://a/c', 'agent': 'https://a/a'}})
        self.assertEqual(snd.call_count, 2)
        self.assertEqual((r['caller_music'], r['caller_sound_s'], r['agent_music']), (18, 20, 0))

    def test_not_asked_when_the_caller_spoke(self):
        vc = self.load()
        sides = iter([{'speech': 6.0, 'beep': 0.0, 'first': 1.0, 'no_data': False},
                      {'speech': 3.0, 'beep': 0.0, 'first': None, 'no_data': False}])
        vad = mock.Mock(side=lambda pcm: next(sides))
        with mock.patch.object(vc, 'fetch', side_effect=['/tmp/c.wav', '/tmp/a.wav']), \
                mock.patch.object(vc, 'decode', return_value=b'\0\0' * 16000), \
                mock.patch.object(vc.os, 'unlink'), \
                mock.patch.object(vc.os.path, 'isfile', return_value=False), \
                mock.patch.object(vc, 'sound_of') as snd:
            r = vc.split_sides(vad, {'id': 'x', 'talk': 20, 'split': {'caller': 'https://a/c', 'agent': 'https://a/a'}})
        snd.assert_not_called()
        self.assertNotIn('caller_music', r)

    def test_without_panns_installed_sound_of_is_none(self):
        vc = self.load()
        with mock.patch.dict('sys.modules', {'panns_sound': None}):
            self.assertIsNone(vc.sound_of(b'\0\0' * 100))
