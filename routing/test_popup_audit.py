"""CH-667: No Voice calls the AI called Valid (audit team, 12 Nov: "65-70%").

The team's own Correct / Wrong marks, 8-11 Nov: 191 marked, 9 wrong - each
one of three kinds:
  1. The caller's computer pop-up talking ("suspicious activity on Facebook,
     please call our support") - the team marks it Only Agent + Beeping; the
     AI called it Valid (or Robocall, which could not be marked).
  2. The caller's side "speaking" 15 s or more and the agent under 1 s: kept
     Valid without the transcript check (71 calls in a week).
  3. The transcript step turning a No Voice call into Valid / Caller OK on a
     word or two (180 in a week, caller 0-0.3 s by Silero; of the 6 the team
     marked, 5 were No Voice).

Run:  python manage.py test routing.test_popup_audit
"""
import importlib.util
import io
import json
from datetime import timedelta

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from campaigns.models import Campaign
from routing import ai_labels
from routing.models import CallLog
from routing.signals import mirror_call_log


def ai_final():
    spec = importlib.util.spec_from_file_location('ai_final_ch667', 'scripts/ai_final/ai_final.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@override_settings(AI_FINAL_SKIP_CALLER_SECONDS=15, VOICE_MIN_SECONDS=1.0)
class LongCallerSideTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='C-07', status='active')
        self.n = 0

    def call(self, caller, agent):
        self.n += 1
        c = CallLog.objects.create(
            organization=self.org, campaign=self.camp, caller_number=f'+1555{self.n:07d}',
            called_number='+18334973067', twilio_call_sid=f's{self.n}', status='completed', duration=41,
            voice_label='voice', voice_channels=2, voice_caller_seconds=caller, voice_agent_seconds=agent,
            ai_label='valid', ai_rule_label='valid', ai_confidence=95, ai_comment='Caller speaks',
            ai_checked_at=timezone.now() - timedelta(minutes=1), ended_at=timezone.now() - timedelta(minutes=2),
            recording_url=f'https://rec.example.com/recordings/s{self.n}.mp3')
        mirror_call_log(c.id)
        return c

    def pending(self):
        out = io.StringIO()
        call_command('ai_final_pending', stdout=out)
        return {json.loads(line)['id'] for line in out.getvalue().splitlines()}

    def test_a_caller_side_talking_alone_gets_the_transcript_check(self):
        # 11 Nov, 03dcdda5: caller 27.8 s, agent 0.0 s - the pop-up; the team: Only Agent + Beeping.
        popup = self.call(27.8, 0.0)
        talk = self.call(27.8, 6.0)           # a real conversation: both sides talk
        self.assertEqual(self.pending(), {str(popup.id)})
        nf = ai_labels.needs_final
        self.assertTrue(nf('completed', 'valid', 27.8, agent_seconds=0.0))
        self.assertTrue(nf('completed', 'valid', 27.8, agent_seconds=0.9))
        self.assertFalse(nf('completed', 'valid', 27.8, agent_seconds=6.0))
        self.assertFalse(nf('completed', 'valid', 27.8))           # agent not measured: as before
        self.assertIsNotNone(talk)


class RobocallAuditedTests(SimpleTestCase):
    def test_robocall_can_be_marked_ivr_and_voice_found_cannot(self):
        self.assertIn(ai_labels.ROBOCALL, ai_labels.AUDITED)
        self.assertNotIn(ai_labels.IVR, ai_labels.AUDITED)
        self.assertNotIn(ai_labels.MIXED, ai_labels.AUDITED)


class InstructionTests(SimpleTestCase):
    def setUp(self):
        self.system = ai_final().SYSTEM

    def test_the_pop_up_voice_is_agent_beep_not_the_caller_and_not_robocall(self):
        s = self.system
        self.assertIn("the caller's computer POP-UP talking", s)
        self.assertIn('A computer pop-up warning playing on the caller\'s side is NOT robocall: it is agent_beep.', s)
        self.assertIn('POP-UP: security-warning text on the caller\'s line', s)

    def test_little_caller_speech_keeps_no_voice(self):
        self.assertIn('LITTLE CALLER SPEECH', self.system)
        self.assertIn('A clear greeting from a live caller in reply to the agent', self.system)
        self.assertIn('even only to say they got a notice or pop-up on their computer', self.system)
        self.assertIn('Silero heard under 1 s of human speech', self.system)

    def test_every_label_still_described_once(self):
        for label in ai_final().LABELS:
            self.assertEqual(self.system.count(f'- {label}:'), 1, label)
