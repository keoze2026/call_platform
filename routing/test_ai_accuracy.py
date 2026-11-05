"""CH-594: AI audit accuracy - agent echo on the caller's line, the LLM's
prompt, temperature and the audit team's corrections as examples, the cost
gate, --redo, and the No Voice alert waiting for the final label.

Run:  python manage.py test routing.test_ai_accuracy
"""
import importlib.util
import io
import json
import time
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from campaigns.models import Campaign
from routing import ai_labels
from routing.models import CallLog
from routing.signals import mirror_call_log

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:                                    # pragma: no cover
    HAVE_NUMPY = False


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = 512 / 16000          # one Silero window, seconds


class EchoTests(SimpleTestCase):
    """The agent's voice coming back on the caller's line is not the caller."""

    def setUp(self):
        if not HAVE_NUMPY:
            self.skipTest('numpy not installed')
        self.vc = load('voice_check', 'scripts/voice_check/voice_check.py')

    @staticmethod
    def sides(n=400):
        return [0.0] * n, [0.001] * n, [0.0] * n, [0.001] * n

    def run_(self, cp, cr, ap, ar):
        return self.vc.echo_free(np, cp, cr, ap, ar)

    def test_echo_of_the_agent_is_taken_out(self):
        cp, cr, ap, ar = self.sides()
        for i in range(0, 100):                     # the agent's opening, loud
            ap[i], ar[i] = 0.95, 0.20
        for i in range(5, 105):                     # 0.15 s later, quiet, on the caller's line
            cp[i], cr[i] = 0.90, 0.03
        speech, first, echo = self.run_(cp, cr, ap, ar)
        self.assertEqual(speech, 0.0)
        self.assertIsNone(first)
        self.assertAlmostEqual(echo, 100 * W, places=1)

    def test_a_real_caller_answering_after_the_agent_counts(self):
        cp, cr, ap, ar = self.sides()
        for i in range(0, 100):
            ap[i], ar[i] = 0.95, 0.20
        for i in range(150, 200):                   # the agent is quiet, the caller talks
            cp[i], cr[i] = 0.92, 0.10
        speech, first, echo = self.run_(cp, cr, ap, ar)
        self.assertAlmostEqual(speech, 50 * W, places=2)
        self.assertAlmostEqual(first, 150 * W, places=2)
        self.assertEqual(echo, 0.0)

    def test_a_caller_talking_over_the_agent_as_loud_counts(self):
        cp, cr, ap, ar = self.sides()
        for i in range(0, 100):
            ap[i], ar[i] = 0.95, 0.20
            cp[i], cr[i] = 0.95, 0.15               # double talk: not 9 dB quieter
        speech, _, echo = self.run_(cp, cr, ap, ar)
        self.assertAlmostEqual(speech, 100 * W, places=2)
        self.assertEqual(echo, 0.0)

    def test_echo_long_after_the_agent_stopped_is_speech(self):
        cp, cr, ap, ar = self.sides()
        for i in range(0, 50):
            ap[i], ar[i] = 0.95, 0.20
        for i in range(120, 140):                   # 2 s after the agent stopped
            cp[i], cr[i] = 0.90, 0.03
        speech, _, echo = self.run_(cp, cr, ap, ar)
        self.assertAlmostEqual(speech, 20 * W, places=2)
        self.assertEqual(echo, 0.0)

    def test_sides_of_different_length_line_up_from_the_end(self):
        cp, cr, ap, ar = self.sides(400)
        ap, ar = ap[:300], ar[:300]                 # the agent's file is 100 windows shorter
        for i in range(0, 100):                     # agent windows 0-99 = caller windows 100-199
            ap[i], ar[i] = 0.95, 0.20
        for i in range(103, 203):
            cp[i], cr[i] = 0.90, 0.03
        speech, _, echo = self.run_(cp, cr, ap, ar)
        self.assertEqual(speech, 0.0)
        self.assertGreater(echo, 3.0)

    def test_no_agent_audio_changes_nothing(self):
        cp, cr, _, _ = self.sides()
        for i in range(10, 60):
            cp[i], cr[i] = 0.9, 0.01
        speech, first, echo = self.run_(cp, cr, [], [])
        self.assertAlmostEqual(speech, 50 * W, places=2)
        self.assertEqual(echo, 0.0)


class LabelTests(SimpleTestCase):
    def test_the_echo_shows_in_the_comment(self):
        r = ai_labels.decide({'channels': 2, 'caller_speech': 0.0, 'agent_speech': 6.0, 'beep': 0,
                              'caller_echo': 4.2})
        self.assertEqual(r['label'], ai_labels.AGENT_ONLY)
        self.assertIn('agent echo on caller line 4.2 s (not counted)', r['comment'])

    def test_no_echo_no_change_in_the_comment(self):
        r = ai_labels.decide({'channels': 2, 'caller_speech': 0.0, 'agent_speech': 6.0, 'beep': 0})
        self.assertTrue(r['comment'].endswith('(caller 0.0 s, agent 6.0 s, beeping 0.0 s)'))

    @override_settings(AI_FINAL_SKIP_CALLER_SECONDS=15)
    def test_who_goes_to_the_llm(self):
        nf = ai_labels.needs_final
        self.assertFalse(nf('completed', 'valid', 40.0))            # a real conversation
        self.assertTrue(nf('completed', 'valid', 3.0))              # a "hello?" - the LLM decides
        self.assertTrue(nf('completed', 'agent_only', 0.0))
        self.assertTrue(nf('completed', 'mixed', None))
        self.assertFalse(nf('no_answer', 'dead_caller', 0.0))        # never transcribed
        self.assertFalse(nf('completed', 'dead_caller', 0.0, fault=True))
        self.assertFalse(nf('completed', 'agent_only', 0.0, has_recording=False))

    @override_settings(AI_FINAL_SKIP_CALLER_SECONDS=0)
    def test_skip_0_sends_every_call(self):
        self.assertTrue(ai_labels.needs_final('completed', 'valid', 400.0))


class PromptTests(SimpleTestCase):
    def setUp(self):
        self.af = load('ai_final', 'scripts/ai_final/ai_final.py')
        self.cfg = self.af.Config({'ANTHROPIC_API_KEY': 'k', 'ASSEMBLYAI_API_KEY': 'a'})

    def test_defaults(self):
        self.assertEqual(self.cfg.max_seconds, 120)
        self.assertEqual(self.cfg.example_count, 12)
        self.assertEqual(self.cfg.model, 'claude-haiku-5-5')

    def test_examples_put_the_teams_corrections_first_in_words(self):
        text = self.af.examples_text([
            {'label': 'agent_only', 'ai_said': 'valid', 'wrong': True, 'note': 'only echo of the agent',
             'transcript': 'Agent: Thank you for calling.\nCaller: thank you for calling', 'measurements': 'm',
             'talk': 21},
            {'label': 'valid', 'ai_said': 'valid', 'wrong': False, 'transcript': '', 'talk': 90},
            {'label': 'nonsense', 'ai_said': 'valid', 'wrong': True},
            {'bad': 1},
        ])
        self.assertIn('the AI said valid, our audit team listened and corrected it to: agent_only', text)
        self.assertIn("Team's note: only echo of the agent", text)
        self.assertIn('our audit team listened and confirmed: valid', text)
        self.assertIn('(no words heard)', text)
        self.assertNotIn('nonsense', text)
        self.assertEqual(self.af.examples_text([]), '')

    def test_claude_gets_the_examples_and_no_temperature(self):
        self.cfg.examples = '\n\nEXAMPLES-HERE'
        sent = {}

        def fake_http(method, url, headers, body=None, timeout=120):
            sent.update(json.loads(body))
            return {'content': [{'type': 'text', 'text': '{"label": "agent_only", "confidence": 91, "summary": "s"}'}]}
        with mock.patch.object(self.af, 'http', fake_http):
            a = self.af.ask_llm(self.cfg, 'call')
        self.assertEqual(a['label'], 'agent_only')
        # CH-600: Claude 5.5 refuses `temperature` ("deprecated for this model")
        self.assertNotIn('temperature', sent)
        self.assertTrue(sent['system'].startswith('You audit pay-per-call'))
        self.assertTrue(sent['system'].endswith('EXAMPLES-HERE'))
        self.assertIn('ECHO', sent['system'])

    def test_examples_loaded_once_per_pass_and_a_failure_is_harmless(self):
        rows = json.dumps({'label': 'dead_caller', 'ai_said': 'agent_only', 'wrong': True, 'transcript': ''})
        with mock.patch.object(self.af.vc, 'manage', return_value=mock.Mock(returncode=0, stdout=rows + '\n')) as m:
            self.af.load_examples(self.cfg)
        self.assertEqual(m.call_args[0][0], ['ai_final_examples', '--limit', '12'])
        self.assertIn('corrected it to: dead_caller', self.cfg.examples)
        with mock.patch.object(self.af.vc, 'manage', side_effect=RuntimeError('docker down')):
            self.af.load_examples(self.cfg)
        self.assertEqual(self.cfg.examples, '')

    def test_the_pre_check_sends_the_same_request_as_the_real_question(self):
        """CH-600: 5 Nov, Claude refused `temperature` - but only on the real
        question, after AssemblyAI was paid. The pre-check now sends the same
        shape, so such a refusal stops the run before anything is spent."""
        sent = []

        def fake_http(method, url, headers, body=None, timeout=120):
            sent.append(json.loads(body))
            return {'content': [{'type': 'text', 'text': '{}'}]}
        with mock.patch.object(self.af, 'http', fake_http):
            self.af.llm_ready(self.cfg)
        self.assertEqual(set(sent[0]), {'model', 'max_tokens', 'system', 'messages'})
        self.assertTrue(sent[0]['system'].startswith('You audit pay-per-call'))

    def test_an_llm_failure_keeps_the_transcript_and_a_retry_reuses_it(self):
        with mock.patch.object(self.af, 'call_audio', return_value=(b'wav', True)), \
                mock.patch.object(self.af, 'transcribe_assemblyai', return_value='[0s] Agent: Thank you for calling.') as stt, \
                mock.patch.object(self.af, 'ask_llm', side_effect=RuntimeError('400 from api.anthropic.com: bad')):
            r = self.af.final_for(self.cfg, {'id': 'c1', 'talk': 9})
        self.assertEqual(stt.call_count, 1)
        self.assertEqual(r['transcript'], '[0s] Agent: Thank you for calling.')
        self.assertIn('400', r['error'])
        with mock.patch.object(self.af, 'call_audio') as audio, \
                mock.patch.object(self.af, 'transcribe_assemblyai') as stt, \
                mock.patch.object(self.af, 'ask_llm', return_value={'label': 'agent_only', 'confidence': 90,
                                                                    'summary': 's'}) as ask:
            r = self.af.final_for(self.cfg, {'id': 'c1', 'talk': 9, 'transcript': '[0s] Agent: Thank you for calling.'})
        audio.assert_not_called()
        stt.assert_not_called()                                           # not paid twice
        self.assertEqual(r['label'], 'agent_only')
        self.assertIn('caller and agent recorded apart', ask.call_args[0][1])

    def test_an_account_problem_still_stops_the_run(self):
        with mock.patch.object(self.af, 'call_audio', return_value=(b'wav', True)), \
                mock.patch.object(self.af, 'transcribe_assemblyai', return_value='x'), \
                mock.patch.object(self.af, 'ask_llm', side_effect=self.af.AccountError('credit balance')):
            with self.assertRaises(self.af.AccountError):
                self.af.final_for(self.cfg, {'id': 'c1', 'talk': 9})

    def test_redo_asks_for_calls_labelled_before_the_run(self):
        listing = mock.Mock(returncode=0, stdout='', stderr='')
        with mock.patch.object(self.af.vc, 'manage', return_value=listing) as m:
            o = type('O', (), {'limit': 5, 'days': 1, 'retry_failed': False, 'id': None, 'dry_run': False,
                               'show_transcript': False, 'redo_before': 1700000000.5})()
            self.assertEqual(self.af.one_pass(o, self.cfg), (0, 0))
        self.assertIn('--redo-before', m.call_args[0][0])
        self.assertIn('1700000000.5', m.call_args[0][0])


@override_settings(VOICE_TELEGRAM_ALERTS=True, REPORTS_BOT_TOKEN='t', AI_FINAL_SKIP_CALLER_SECONDS=15,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class BackendTests(TestCase):
    def setUp(self):
        from analytics.models import TeamReportChat
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='C-07', status='active')
        TeamReportChat.objects.create(organization=self.org, chat_id='-100', title='Team')
        self.n = 0

    def call(self, label='valid', **kw):
        self.n += 1
        fields = dict(organization=self.org, campaign=self.camp, caller_number=f'+1555{self.n:07d}',
                      called_number='+18334973067', twilio_call_sid=f's{self.n}', status='completed',
                      duration=40, voice_label='voice', ai_label=label, ai_confidence=95,
                      ai_comment='c', ai_checked_at=timezone.now() - timedelta(minutes=1),
                      ended_at=timezone.now() - timedelta(minutes=2),
                      recording_url=f'https://rec.example.com/recordings/s{self.n}.mp3')
        fields.update(kw)
        c = CallLog.objects.create(**fields)
        mirror_call_log(c.id)
        return c

    def pending(self, *args):
        out = io.StringIO()
        call_command('ai_final_pending', *args, stdout=out)
        return [json.loads(line)['id'] for line in out.getvalue().splitlines()]

    def final(self, *rows):
        with mock.patch('sys.stdin', io.StringIO(''.join(json.dumps(r) + '\n' for r in rows))), \
                mock.patch('analytics.team_report.telegram') as tg:
            out = io.StringIO()
            call_command('ai_final_results', stdout=out)
        return out.getvalue(), tg

    def rule(self, *rows):
        with mock.patch('sys.stdin', io.StringIO(''.join(json.dumps(r) + '\n' for r in rows))), \
                mock.patch('analytics.team_report.telegram') as tg:
            out = io.StringIO()
            call_command('voice_results', stdout=out)
        return out.getvalue(), tg

    # -- the cost gate ------------------------------------------------------
    def test_a_real_conversation_is_not_sent_to_the_llm(self):
        talk = self.call('valid', voice_caller_seconds=42.0)
        hello = self.call('valid', voice_caller_seconds=2.0)
        silent = self.call('agent_only', voice_caller_seconds=0.0)
        self.assertEqual(set(self.pending()), {str(hello.id), str(silent.id)})
        self.assertEqual(self.pending('--id', str(talk.id)), [str(talk.id)])     # named: always

    def test_redo_takes_labelled_calls_once(self):
        c = self.call('valid', voice_caller_seconds=1.0)
        self.final({'id': str(c.id), 'label': 'caller_ok', 'confidence': 70, 'summary': 's', 'by': 'x'})
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.pending('--redo-before', str(time.time() + 1)), [str(c.id)])
        self.assertEqual(self.pending('--redo-before', str(time.time() - 3600)), [])   # done after that

    def test_a_failed_try_keeps_its_transcript_and_is_retried_once_per_run(self):
        c = self.call('agent_only', voice_caller_seconds=0.0)
        self.final({'id': str(c.id), 'error': '400 from api.anthropic.com: bad', 'transcript': 'Agent: Hello?'})
        c.refresh_from_db()
        self.assertEqual(c.ai_transcript, 'Agent: Hello?')
        out = io.StringIO()
        call_command('ai_final_pending', '--retry-failed', '--redo-before', str(time.time() + 1), stdout=out)
        items = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([(i['id'], i['transcript']) for i in items], [(str(c.id), 'Agent: Hello?')])
        self.assertEqual(self.pending('--retry-failed', '--redo-before', str(time.time() - 3600)), [])

    # -- the team's corrections ----------------------------------------------
    def test_examples_wrong_first(self):
        now = timezone.now()
        self.call('valid', ai_review_label='valid', ai_reviewed_at=now, ai_transcript='Caller: I need help')
        self.call('valid', ai_review_label='agent_only', ai_review_note='echo', ai_reviewed_at=now - timedelta(hours=1),
                  ai_transcript='Agent: Thank you for calling')
        self.call('dead_caller')                                                   # not reviewed
        out = io.StringIO()
        call_command('ai_final_examples', '--limit', '3', stdout=out)
        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([(r['label'], r['wrong']) for r in rows], [('agent_only', True), ('valid', False)])
        self.assertEqual(rows[0]['ai_said'], 'valid')
        self.assertEqual(rows[0]['note'], 'echo')

    # -- the alert waits for the final label ---------------------------------
    def test_without_the_switch_the_rule_label_alerts_at_once(self):
        c = self.call('', ai_checked_at=None, voice_label='')
        out, tg = self.rule({'id': str(c.id), 'channels': 2, 'speech': 5, 'audio': 20,
                             'caller_speech': 0.0, 'agent_speech': 5.0, 'beep': 0})
        self.assertTrue(tg.called)

    @override_settings(AI_FINAL_ENABLED=True)
    def test_with_the_switch_the_alert_waits_for_the_llm(self):
        c = self.call('', ai_checked_at=None, voice_label='')
        _, tg = self.rule({'id': str(c.id), 'channels': 2, 'speech': 5, 'audio': 20,
                           'caller_speech': 0.0, 'agent_speech': 5.0, 'beep': 0})
        self.assertFalse(tg.called)                                    # held back
        c.refresh_from_db()
        self.assertEqual(c.ai_label, 'agent_only')
        out, tg = self.final({'id': str(c.id), 'label': 'agent_only', 'confidence': 90, 'summary': 'Agent only.',
                              'transcript': '', 'by': 'x'})
        self.assertTrue(tg.called)                                     # sent now, once
        self.assertIn('AI (90% sure): <i>Agent only.</i>', tg.call_args[0][1]['text'])
        _, tg = self.final({'id': str(c.id), 'label': 'agent_only', 'confidence': 90, 'summary': 'Agent only.',
                            'transcript': '', 'by': 'x'})
        self.assertFalse(tg.called)                                    # a redo sends nothing again

    @override_settings(AI_FINAL_ENABLED=True)
    def test_the_llm_saying_valid_means_no_alert_at_all(self):
        c = self.call('', ai_checked_at=None, voice_label='')
        _, tg1 = self.rule({'id': str(c.id), 'channels': 2, 'speech': 5, 'audio': 20,
                            'caller_speech': 0.0, 'agent_speech': 5.0, 'beep': 0})
        _, tg2 = self.final({'id': str(c.id), 'label': 'valid', 'confidence': 85, 'summary': 's', 'by': 'x'})
        self.assertFalse(tg1.called or tg2.called)

    @override_settings(AI_FINAL_ENABLED=True)
    def test_if_the_llm_fails_the_held_alert_still_goes(self):
        c = self.call('', ai_checked_at=None, voice_label='')
        self.rule({'id': str(c.id), 'channels': 2, 'speech': 5, 'audio': 20,
                   'caller_speech': 0.0, 'agent_speech': 5.0, 'beep': 0})
        _, tg = self.final({'id': str(c.id), 'error': 'AssemblyAI: timeout'})
        self.assertTrue(tg.called)
        _, tg = self.final({'id': str(c.id), 'error': 'AssemblyAI: timeout'})
        self.assertFalse(tg.called)                                    # a retry does not repeat it

    @override_settings(AI_FINAL_ENABLED=True)
    def test_a_call_the_llm_never_sees_alerts_at_once(self):
        c = self.call('', ai_checked_at=None, voice_label='', status='no_answer')
        _, tg = self.rule({'id': str(c.id), 'channels': 2, 'speech': 0, 'audio': 20,
                           'caller_speech': 0.0, 'agent_speech': 0.0, 'beep': 0})
        self.assertTrue(tg.called)
