"""CH-555: transcript + LLM final label (scripts/ai_final, ai_final_pending /
ai_final_results), against stand-in AssemblyAI, Deepgram, Claude and GPT
servers that answer like the real ones. No real provider, key or call.

Run:  python manage.py test routing.test_ai_final
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from analytics.models import CallRecord
from campaigns.models import Campaign
from routing.models import CallLog
from routing.signals import mirror_call_log

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:                                    # pragma: no cover
    HAVE_NUMPY = False


def load():
    spec = importlib.util.spec_from_file_location('ai_final', 'scripts/ai_final/ai_final.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Fake(BaseHTTPRequestHandler):
    """AssemblyAI, Deepgram, Anthropic and OpenAI on one port, by path."""
    seen = []
    llm_label = 'agent_only'
    no_credit = False

    def log_message(self, *a):
        pass

    def body(self):
        n = int(self.headers.get('content-length') or 0)
        return self.rfile.read(n) if n else b''

    def reply(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        b = self.body()
        Fake.seen.append(('POST', self.path, dict(self.headers), b[:4] if self.path.endswith('upload') else b))
        if self.path == '/v2/upload':
            return self.reply({'upload_url': 'https://cdn/x'})
        if self.path == '/v2/transcript':
            return self.reply({'id': 't1', 'status': 'queued'})
        if self.path.startswith('/v1/listen'):
            return self.reply({'results': {'utterances': [
                {'channel': 1, 'start': 0.4, 'transcript': 'Thank you for calling, how can I help?'},
                {'channel': 0, 'start': 3.2, 'transcript': 'Hi, I need a plumber.'}]}})
        if self.path == '/v1/messages' and Fake.no_credit:
            return self.reply({'type': 'error', 'error': {'type': 'invalid_request_error', 'message':
                               'Your credit balance is too low to access the Anthropic API.'}}, 400)
        if self.path == '/v1/messages':
            return self.reply({'content': [{'type': 'text', 'text': json.dumps(
                {'label': Fake.llm_label, 'confidence': 88, 'summary': 'Agent greeted twice; the caller never spoke.'})}]})
        if self.path == '/v1/chat/completions':
            return self.reply({'choices': [{'message': {'content': json.dumps(
                {'label': 'valid', 'confidence': 93, 'summary': 'Caller asked for a plumber and booked a visit.'})}}]})
        self.reply({'error': 'no'}, 404)

    def do_GET(self):
        Fake.seen.append(('GET', self.path, dict(self.headers), b''))
        self.reply({'id': 't1', 'status': 'completed', 'text': 'hello', 'utterances': [
            {'channel': '2', 'start': 400, 'text': 'Thank you for calling.'},
            {'channel': '2', 'start': 5000, 'text': 'Hello? Are you there?'}]})

    def do_DELETE(self):
        Fake.seen.append(('DELETE', self.path, dict(self.headers), b''))
        self.reply({'id': 't1', 'status': 'completed'})


@unittest.skipUnless(HAVE_NUMPY and shutil.which('ffmpeg'), 'numpy / ffmpeg not installed')
class PipelineTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        super().tearDownClass()

    def setUp(self):
        Fake.seen = []
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        os.makedirs(os.path.join(self.dir, 'split'))
        for who, src in (('caller', 'anullsrc=r=8000:cl=mono'), ('agent', 'sine=f=440:r=8000')):
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', src, '-t', '3',
                            os.path.join(self.dir, 'split', f'c1-{who}.wav')], check=True)
        env = {'AI_FINAL_TEST': '1', 'AAI_BASE': self.base, 'DG_PRERECORDED_URL': f'{self.base}/v1/listen',
               'ANTHROPIC_URL': f'{self.base}/v1/messages', 'OPENAI_URL': f'{self.base}/v1/chat/completions'}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.af = load()
        self.af.vc.RECORDINGS_DIR = self.dir

    def cfg(self, **env):
        base = {'AI_STT_PROVIDER': 'assemblyai', 'AI_LLM_PROVIDER': 'anthropic', 'ASSEMBLYAI_API_KEY': 'aai',
                'DEEPGRAM_API_KEY': 'dg', 'ANTHROPIC_API_KEY': 'ant', 'OPENAI_API_KEY': 'oai'}
        return self.af.Config({**base, **env})

    item = {'id': 'c1', 'talk': 3, 'file': 'c1.mp3', 'url': '',
            'rule': {'label': 'agent_only', 'comment': 'Agent speaks, caller silent, no beeping (caller 0.0 s, '
                                                      'agent 3.0 s, beeping 0.0 s)'}}

    def test_assemblyai_and_claude(self):
        r = self.af.final_for(self.cfg(), self.item)
        self.assertEqual((r['label'], r['confidence']), ('agent_only', 88))
        self.assertEqual(r['summary'], 'Agent greeted twice; the caller never spoke.')
        self.assertEqual(r['transcript'], '[0s] Agent: Thank you for calling.\n[5s] Agent: Hello? Are you there?')
        self.assertEqual(r['by'], 'assemblyai + claude-haiku-5-5')
        calls = [(m, p) for m, p, *_ in Fake.seen]
        self.assertIn(('DELETE', '/v2/transcript/t1'), calls)            # nothing left with AssemblyAI
        job = json.loads(next(b for m, p, h, b in Fake.seen if p == '/v2/transcript' and m == 'POST'))
        self.assertTrue(job['multichannel'])                            # caller and agent apart
        upload = next(h for m, p, h, b in Fake.seen if p == '/v2/upload')
        self.assertEqual({k.lower(): v for k, v in upload.items()}['authorization'], 'aai')
        msg = next((h, b) for m, p, h, b in Fake.seen if p == '/v1/messages')
        self.assertEqual({k.lower(): v for k, v in msg[0].items()}['x-api-key'], 'ant')
        sent = json.loads(msg[1])
        self.assertEqual(sent['model'], 'claude-haiku-5-5')
        self.assertIn('Rule label (Silero / PANNs): agent_only', sent['messages'][0]['content'])
        self.assertIn('Agent: Hello? Are you there?', sent['messages'][0]['content'])

    def test_deepgram_and_gpt(self):
        r = self.af.final_for(self.cfg(AI_STT_PROVIDER='deepgram', AI_LLM_PROVIDER='openai',
                                       AI_LLM_MODEL='gpt-test'), self.item)
        self.assertEqual((r['label'], r['confidence']), ('valid', 93))
        self.assertEqual(r['transcript'], '[0s] Agent: Thank you for calling, how can I help?\n'
                                          '[3s] Caller: Hi, I need a plumber.')
        dg = next(p for m, p, h, b in Fake.seen if p.startswith('/v1/listen'))
        self.assertIn('mip_opt_out=true', dg)
        self.assertIn('multichannel=true', dg)
        self.assertIn('model=nova-3', dg)
        gpt = json.loads(next(b for m, p, h, b in Fake.seen if p == '/v1/chat/completions'))
        self.assertEqual(gpt['model'], 'gpt-test')
        self.assertEqual(gpt['response_format'], {'type': 'json_object'})

    def test_one_recording_when_the_sides_are_not_there(self):
        shutil.rmtree(os.path.join(self.dir, 'split'))
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=f=300:r=8000', '-t', '3',
                        os.path.join(self.dir, 'c1.mp3')], check=True)
        r = self.af.final_for(self.cfg(), self.item)
        job = json.loads(next(b for m, p, h, b in Fake.seen if p == '/v2/transcript' and m == 'POST'))
        self.assertFalse(job['multichannel'])
        self.assertTrue(r['by'].endswith('(one channel)'))

    def test_an_unknown_label_is_an_error_not_saved(self):
        Fake.llm_label = 'maybe'
        try:
            with self.assertRaisesRegex(RuntimeError, 'unknown label'):
                self.af.final_for(self.cfg(), self.item)
        finally:
            Fake.llm_label = 'agent_only'

    def test_no_credit_stops_before_anything_is_transcribed_or_marked(self):
        Fake.no_credit = True
        saved = []
        try:
            listing = mock.Mock(returncode=0, stdout=json.dumps(self.item) + '\n', stderr='')
            with mock.patch.object(self.af.vc, 'manage', side_effect=lambda args, stdin=None: (
                    saved.append(stdin) or mock.Mock(returncode=0, stdout='', stderr='')) if args[0] == 'ai_final_results'
                    else listing):
                o = type('O', (), {'limit': 5, 'days': 2, 'retry_failed': False, 'id': None, 'dry_run': False,
                                   'show_transcript': False})()
                done, rc = self.af.one_pass(o, self.cfg())
        finally:
            Fake.no_credit = False
        self.assertEqual((done, rc), (0, 2))
        self.assertEqual(saved, [])                                       # no call marked failed
        self.assertFalse([p for m, p, *_ in Fake.seen if p.startswith('/v2/')])   # nothing transcribed

    def test_encrypted_connections_only(self):
        with mock.patch.dict(os.environ, {'AI_FINAL_TEST': ''}):
            with self.assertRaisesRegex(RuntimeError, 'unencrypted'):
                self.af.http('GET', 'http://example.com/x', {})

    def test_not_ready_without_keys(self):
        cfg = self.af.Config({'AI_STT_PROVIDER': 'deepgram', 'AI_LLM_PROVIDER': 'openai'})
        p = ' | '.join(cfg.problems())
        self.assertIn('no DEEPGRAM_API_KEY', p)
        self.assertIn('no OPENAI_API_KEY', p)
        self.assertIn('AI_LLM_MODEL is not set', p)                     # GPT needs the model named


@override_settings(VOICE_TELEGRAM_ALERTS=True, REPORTS_BOT_TOKEN='t',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class SaveTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='C-07', status='active')
        self.n = 0

    def call(self, label='valid', **kw):
        self.n += 1
        fields = dict(organization=self.org, campaign=self.camp, caller_number=f'+1555{self.n:07d}',
                      called_number='+18334973067', twilio_call_sid=f's{self.n}', status='completed',
                      duration=40, voice_label='voice', ai_label=label, ai_confidence=95,
                      ai_comment='Caller speaks', ai_checked_at=timezone.now() - timedelta(minutes=1),
                      ended_at=timezone.now() - timedelta(minutes=2),
                      recording_url=f'https://rec.example.com/recordings/s{self.n}.mp3')
        fields.update(kw)
        c = CallLog.objects.create(**fields)
        mirror_call_log(c.id)
        return c

    def pending(self, *args):
        out = io.StringIO()
        call_command('ai_final_pending', *args, stdout=out)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def save(self, *rows):
        with mock.patch('sys.stdin', io.StringIO(''.join(json.dumps(r) + '\n' for r in rows))), \
                mock.patch('analytics.team_report.telegram') as tg:
            out = io.StringIO()
            call_command('ai_final_results', stdout=out)
        return out.getvalue(), tg

    def test_pending_lists_checked_calls_once(self):
        c = self.call()
        self.call(ai_checked_at=None)                                   # Silero has not listened yet
        items = self.pending()
        self.assertEqual([i['id'] for i in items], [str(c.id)])
        self.assertEqual(items[0]['rule']['label'], 'valid')
        self.assertIn('split', items[0])
        self.save({'id': str(c.id), 'label': 'valid', 'confidence': 90, 'summary': 's', 'transcript': 't', 'by': 'x'})
        self.assertEqual(self.pending(), [])

    def test_the_final_label_replaces_the_rule_label_which_is_kept(self):
        c = self.call('valid')
        out, tg = self.save({'id': str(c.id), 'label': 'agent_only', 'confidence': 81,
                             'summary': 'Agent  greeted;\nthe caller never spoke.', 'transcript': 'Agent: Hello?',
                             'by': 'assemblyai + claude-haiku-5-5'})
        c.refresh_from_db()
        self.assertEqual((c.ai_label, c.ai_rule_label, c.ai_confidence), ('agent_only', 'valid', 81))
        self.assertEqual(c.ai_summary, 'Agent greeted; the caller never spoke.')
        self.assertEqual(c.ai_transcript, 'Agent: Hello?')
        rec = CallRecord.objects.get(pk=c.pk)
        self.assertEqual((rec.ai_label, rec.ai_confidence, rec.ai_summary),
                         ('agent_only', 81, 'Agent greeted; the caller never spoke.'))
        self.assertIn('saved 1', out)

    def test_a_new_no_voice_from_the_llm_goes_to_telegram(self):
        from analytics.models import TeamReportChat
        TeamReportChat.objects.create(organization=self.org, chat_id='-100', title='Team')
        c = self.call('valid')
        out, tg = self.save({'id': str(c.id), 'label': 'agent_only', 'confidence': 81, 'summary': 's',
                             'transcript': '', 'by': 'x'})
        self.assertTrue(tg.called)
        self.assertIn('telegram 1', out)

    def test_a_failed_try_keeps_the_rule_label_and_can_be_retried(self):
        c = self.call('dead_caller')
        out, _ = self.save({'id': str(c.id), 'error': '401 from api.anthropic.com: invalid key'})
        c.refresh_from_db()
        self.assertEqual(c.ai_label, 'dead_caller')
        self.assertTrue(c.ai_final_by.startswith('error: 401'))
        self.assertEqual(self.pending(), [])
        self.assertEqual([i['id'] for i in self.pending('--retry-failed')], [str(c.id)])
        self.assertIn('failed 1', out)

    def test_an_unknown_label_is_refused(self):
        c = self.call('valid')
        self.save({'id': str(c.id), 'label': 'great', 'confidence': 99, 'summary': 's', 'by': 'x'})
        c.refresh_from_db()
        self.assertEqual(c.ai_label, 'valid')
        self.assertIn('error', c.ai_final_by)

    def test_a_recheck_after_the_final_label_only_updates_the_rule_label(self):
        c = self.call('valid')
        self.save({'id': str(c.id), 'label': 'robocall', 'confidence': 77, 'summary': 'Recorded message.',
                   'transcript': 'Caller: press 1', 'by': 'x'})
        line = json.dumps({'id': str(c.id), 'channels': 1, 'speech': 9.0, 'audio': 40}) + '\n'
        with mock.patch('sys.stdin', io.StringIO(line)), mock.patch('analytics.team_report.telegram'):
            call_command('voice_results', stdout=io.StringIO())
        c.refresh_from_db()
        self.assertEqual((c.ai_label, c.ai_rule_label, c.ai_confidence), ('robocall', 'mixed', 77))

    def test_call_log_shows_summary_and_verdict_to_the_audit_team_only(self):
        from rest_framework_simplejwt.tokens import AccessToken
        from accounts.models import User
        from django.test import Client
        c = self.call('valid')
        self.save({'id': str(c.id), 'label': 'caller_ok', 'confidence': 70, 'summary': 'Wrong number.',
                   'transcript': '', 'by': 'x'})
        audit = User.objects.create(username='a', email='audit@x.io', organization=self.org, role='admin')
        ops = User.objects.create(username='o', email='ops@x.io', organization=self.org, role='admin')
        day = timezone.now().date().isoformat()
        with self.settings(AI_AUDIT_EMAILS='audit@x.io', ALLOWED_HOSTS=['*']):
            post = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(audit)}').post(
                f'/api/ai-audit/calls/{c.id}/review', data='{"correct": true}', content_type='application/json')
            self.assertEqual(post.status_code, 200, post.content)
            row = lambda u: Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}').get(
                f'/api/analytics/calls?date_from={day}&date_to={day}&timezone=UTC').json()['items'][0]
            mine = row(audit)
            self.assertEqual((mine['ai_label'], mine['ai_confidence'], mine['ai_summary'], mine['ai_review_label']),
                             ('caller_ok', 70, 'Wrong number.', 'caller_ok'))
            theirs = row(ops)
            self.assertEqual((theirs['ai_label'], theirs['ai_confidence'], theirs['ai_summary'],
                              theirs['ai_review_label']), (None, None, None, None))
