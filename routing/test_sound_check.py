"""CH-552: PANNs sound check - finding named calls, and what it prints.

PANNs itself (PyTorch) is not installed in CI: a stand-in model gives fixed
probabilities, so the plumbing (sides, talk part, seconds per kind of sound)
is what is tested here.

Run:  python manage.py test routing.test_sound_check
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from accounts.models import Organization
from campaigns.models import Campaign
from routing.models import CallLog

try:
    import numpy as np
except ImportError:                                   # pragma: no cover
    np = None


def load():
    sys.path.insert(0, 'scripts/voice_check')
    spec = importlib.util.spec_from_file_location('sound_check', 'scripts/sound_check/sound_check.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class NamedCallsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='C-07', status='active')
        self.n = 0

    def call(self, caller, **kw):
        self.n += 1
        return CallLog.objects.create(organization=self.org, campaign=self.camp, caller_number=caller,
                                      called_number='+18334973067', twilio_call_sid=f's{self.n}',
                                      status='completed', duration=45, voice_label=kw.pop('voice_label', 'voice'),
                                      ended_at=timezone.now() - timedelta(minutes=30),
                                      recording_url=f'https://rec.example.com/recordings/s{self.n}.wav', **kw)

    def listed(self, *args):
        out = io.StringIO()
        call_command('voice_pending', *args, stdout=out)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_by_caller_number_in_any_format_checked_or_not(self):
        ivr = self.call('+13134598968')                     # already labelled: still listed
        self.call('+15550000000')
        items = self.listed('--caller', '13134598968')
        self.assertEqual([i['id'] for i in items], [str(ivr.id)])
        self.assertEqual(items[0]['caller'], '+13134598968')
        self.assertEqual(items[0]['talk'], 45)
        self.assertEqual([i['id'] for i in self.listed('--caller', '(313) 459-8968')], [str(ivr.id)])
        self.assertEqual([i['id'] for i in self.listed('--id', str(ivr.id))], [str(ivr.id)])

    def test_nothing_usable_lists_nothing(self):
        self.call('+13134598968')
        self.assertEqual(self.listed('--caller', '12'), [])

    def test_the_normal_list_is_unchanged(self):
        waiting = self.call('+13134598968', voice_label='')
        self.call('+15550000000')                            # checked: not pending
        self.assertEqual([i['id'] for i in self.listed()], [str(waiting.id)])


class FakeTagger:
    """Stand-in PANNs: every window is mostly music, a little silence."""

    def __init__(self):
        self.labels = ['Speech', 'Music', 'Beep, bleep', 'Silence', 'Static']
        self.index = {'speech': [0], 'music': [1], 'tone': [2], 'silence': [3], 'noise': [4]}

    def windows(self, audio):
        n = len(audio) // (2 * 32000)
        return np.tile(np.array([[0.02, 0.81, 0.01, 0.35, 0.0]], dtype=np.float32), (n, 1))


class FakeVad:
    def side(self, pcm):
        return {'speech': 0.0, 'audio': len(pcm) / 32000, 'beep': 0.0, 'first': None, 'no_data': False}


@unittest.skipUnless(np is not None and shutil.which('ffmpeg'), 'numpy / ffmpeg not installed')
class TryTests(SimpleTestCase):
    def setUp(self):
        self.sc = load()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=f=440:r=8000', '-t', '20',
                        os.path.join(self.dir, 'c1.wav')], check=True)

    def run_try(self, item):
        sc = self.sc
        listing = mock.Mock(returncode=0, stdout=json.dumps(item) + '\n', stderr='')
        out = io.StringIO()
        with mock.patch.object(sc.vc, 'manage', return_value=listing) as manage, \
                mock.patch.object(sc.vc, 'RECORDINGS_DIR', self.dir), \
                mock.patch.object(sc, 'Tagger', FakeTagger), \
                mock.patch.object(sc.vc, 'Vad', lambda path: FakeVad()), \
                mock.patch.object(sc.os, 'nice'), \
                mock.patch('sys.stdout', out):
            code = sc.main(['try', '--caller', '13134598968'])
        return code, out.getvalue(), manage.call_args[0][0]

    def test_mixed_recording_music_only(self):
        code, text, cmd = self.run_try({'id': 'c1', 'file': 'c1.wav', 'url': '', 'talk': 10, 'caller': '+13134598968'})
        self.assertEqual(code, 0)
        self.assertIn('--caller', cmd)
        self.assertIn('one mixed recording', text)
        # talk 10 s -> the last 11 s of the 20 s file -> 5 two-second windows
        self.assertIn('mixed  Silero speech 0.0 s | PANNs over 10 s: speech 0 s, music 10 s, tone 0 s, '
                      'silence 10 s, noise 0 s', text)
        self.assertIn('heard most: Music 0.81, Silence 0.35', text)

    def test_split_files_are_used_when_there(self):
        os.makedirs(os.path.join(self.dir, 'split'))
        for who in ('caller', 'agent'):
            shutil.copy(os.path.join(self.dir, 'c1.wav'), os.path.join(self.dir, 'split', f'c1-{who}.wav'))
        code, text, _ = self.run_try({'id': 'c1', 'file': 'c1.wav', 'url': '', 'talk': None, 'caller': 'x'})
        self.assertIn('caller and agent apart', text)
        self.assertIn('caller Silero speech', text)
        self.assertIn('agent  Silero speech', text)
