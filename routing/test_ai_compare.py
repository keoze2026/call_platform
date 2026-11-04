"""CH-549: the speech-to-text test tool (scripts/ai_compare/compare_stt.py),
run end to end against stand-in Deepgram and AssemblyAI servers that speak
each provider's streaming protocol. No real provider, key or call is used.

Run:  python manage.py test routing.test_ai_compare
"""
import asyncio
import csv
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest

from django.test import SimpleTestCase

try:
    import websockets  # noqa: F401
    from websockets.asyncio.server import serve
    HAVE_WS = True
except Exception:                                     # not installed: these tests are skipped
    HAVE_WS = False
HAVE_FFMPEG = shutil.which('ffmpeg') is not None


def load():
    spec = importlib.util.spec_from_file_location('compare_stt', 'scripts/ai_compare/compare_stt.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeVad:
    """Silero stand-in: the caller of call 'b' is silent, everyone else talks."""

    def side(self, pcm):
        loud = max(pcm[::2] or b'\0') > 0
        return {'speech': 3.0 if loud else 0.0, 'audio': len(pcm) / 32000, 'beep': 0.0}


class Providers:
    """Deepgram on one port, AssemblyAI on another, in a background loop."""

    def __init__(self):
        self.seen = []
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(5)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start())
        self.ready.set()
        self.loop.run_forever()

    async def _start(self):
        self.dg = await serve(self.deepgram, '127.0.0.1', 0)
        self.aai = await serve(self.assemblyai, '127.0.0.1', 0)
        self.dg_port = self.dg.sockets[0].getsockname()[1]
        self.aai_port = self.aai.sockets[0].getsockname()[1]

    async def deepgram(self, ws):
        self.seen.append(('deepgram', ws.request.path, ws.request.headers.get('Authorization')))
        async for msg in ws:
            if isinstance(msg, bytes):
                continue
            if json.loads(msg).get('type') == 'CloseStream':
                break
        await ws.send(json.dumps({'type': 'Results', 'is_final': True, 'start': 0, 'duration': 1,
                                  'channel': {'alternatives': [{
                                      'transcript': 'Hello I need a plumber' if self.talk else '',
                                      'words': [{'word': 'plumber', 'end': 0.5}]}]}}))
        await ws.close()

    async def assemblyai(self, ws):
        self.seen.append(('assemblyai', ws.request.path, ws.request.headers.get('Authorization')))
        await ws.send(json.dumps({'type': 'Begin', 'id': 'x'}))
        async for msg in ws:
            if isinstance(msg, bytes):
                continue
            if json.loads(msg).get('type') == 'Terminate':
                break
        if self.talk:
            words = [{'text': 'hello', 'start': 100, 'end': 400}]
            await ws.send(json.dumps({'type': 'Turn', 'turn_order': 0, 'end_of_turn': True,
                                      'turn_is_formatted': False, 'transcript': 'hello i need a plummer',
                                      'words': words}))
            await ws.send(json.dumps({'type': 'Turn', 'turn_order': 0, 'end_of_turn': True,
                                      'turn_is_formatted': True, 'transcript': 'Hello, I need a plummer.',
                                      'words': words}))
        await ws.send(json.dumps({'type': 'Termination', 'audio_duration_seconds': 1,
                                  'session_duration_seconds': 36}))
        await ws.close()

    talk = True


@unittest.skipUnless(HAVE_WS and HAVE_FFMPEG, 'websockets / ffmpeg not installed')
class CompareSttTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.providers = Providers()

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base)
        os.makedirs(os.path.join(self.base, 'split'))
        env = {'AI_COMPARE_DIR': self.base, 'AI_COMPARE_TEST': '1',
               'DG_URL': f'ws://127.0.0.1:{self.providers.dg_port}/v1/listen',
               'AAI_URL': f'ws://127.0.0.1:{self.providers.aai_port}/v3/ws'}
        self.old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(self._restore)
        self.cs = load()
        for cid in ('a', 'b'):
            for side in ('caller', 'agent'):
                src = 'anullsrc=r=8000:cl=mono' if (cid, side) == ('b', 'caller') else 'sine=f=440:r=8000'
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', src, '-t', '0.5', '-ac', '1',
                                '-ar', '8000', os.path.join(self.base, 'split', f'{cid}-{side}.wav')], check=True)

    def _restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_tool(self, *extra):
        ns = type('A', (), {'limit': 200, 'concurrency': 3, 'providers': 'deepgram,assemblyai', 'speed': 20.0,
                           'max_minutes': self.max_minutes})()
        keys = {'DEEPGRAM_API_KEY': 'dg-test', 'ASSEMBLYAI_API_KEY': 'aai-test'}
        return asyncio.run(self.cs.run(ns, keys, vad=FakeVad()))

    max_minutes = 0

    def test_long_calls_are_cut_the_same_for_everyone_and_costed_in_full(self):
        self.max_minutes = 0.25 / 60                         # first 0.25 s of the 0.5 s files
        self.run_tool()
        with open(os.path.join(self.base, 'results', 'silero.jsonl')) as f:
            rows = [json.loads(line) for line in f]
        self.assertTrue(all(r['audio'] == 0.25 and r['audio_full'] == 0.5 for r in rows), rows)
        self.assertTrue(all(r['audio_s'] == 0.25 for r in self.streams()))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.cs.report(type('A', (), {'reference': ''})())
        self.assertIn('whole call both sides (0.0 min on average)', buf.getvalue())

    def streams(self):
        with open(os.path.join(self.base, 'results', 'streams.jsonl')) as f:
            return [json.loads(line) for line in f]

    def test_every_side_goes_to_both_providers_opted_out_and_with_their_auth(self):
        self.providers.seen.clear()
        self.assertEqual(self.run_tool(), 0)
        rows = self.streams()
        self.assertEqual(len(rows), 8)                              # 2 calls x 2 sides x 2 providers
        self.assertTrue(all(r['ok'] for r in rows), rows)
        dg = [s for s in self.providers.seen if s[0] == 'deepgram']
        aai = [s for s in self.providers.seen if s[0] == 'assemblyai']
        self.assertTrue(all('model=nova-3' in p and 'mip_opt_out=true' in p and 'sample_rate=16000' in p
                            for _, p, _a in dg))
        self.assertEqual({a for *_, a in dg}, {'Token dg-test'})
        self.assertTrue(all('encoding=pcm_s16le' in p for _, p, _a in aai))
        self.assertEqual({a for *_, a in aai}, {'aai-test'})
        a = [r for r in rows if r['provider'] == 'assemblyai'][0]
        self.assertEqual(a['segments'][0]['text'], 'Hello, I need a plummer.')   # the formatted turn
        self.assertEqual(a['session_s'], 36.0)                       # billed connection time
        self.assertAlmostEqual(a['usd'], 36 / 3600 * 0.15, places=6)

    def test_a_second_run_only_does_what_is_missing(self):
        self.run_tool()
        before = len(self.streams())
        self.run_tool()
        self.assertEqual(len(self.streams()), before)

    def test_encrypted_connections_only_outside_tests(self):
        os.environ.pop('AI_COMPARE_TEST')
        self.cs = load()
        with self.assertRaises(AssertionError):
            asyncio.run(self.cs.stream_deepgram('k', b'\0' * 3200, 20))

    def test_sheet_and_report(self):
        self.run_tool()
        self.assertEqual(self.cs.sheet(type('A', (), {'count': 50})()), 0)
        ref = os.path.join(self.base, 'reference', 'reference.csv')
        with open(ref) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual({r['call_id'] for r in rows}, {'a', 'b'})
        self.assertTrue(os.path.exists(os.path.join(self.base, 'reference', 'caller-audio-50.zip')))
        for r in rows:
            r['caller_said'] = 'hello I need a plumber' if r['call_id'] == 'a' else ''
        with open(ref, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.cs.report(type('A', (), {'reference': ref})())
        text = buf.getvalue()
        self.assertIn('== deepgram ==', text)
        self.assertIn('== assemblyai ==', text)
        # call b's caller is silent: both stand-ins still return words -> "made up"
        self.assertIn('caller silent (Silero) on 1 calls: words made up on 1', text)
        # deepgram: exact words; assemblyai: "plummer" for "plumber" = 1 of 5 words
        dg, aai = text.split('== assemblyai ==')
        self.assertIn('word error rate on caller vs audit team (1 calls, 5 words): 0.0%', dg)
        self.assertIn('(1 calls, 5 words): 20.0%', aai)
        self.assertIn('calls the audit team heard no caller words: 1, words made up on 1', aai)


class WerTests(SimpleTestCase):
    def test_word_error_rate(self):
        cs = load()
        self.assertEqual(cs.wer('Hello, I need a plumber.', 'hello i need a plumber'), (0, 5))
        self.assertEqual(cs.wer('call me at 555', 'call me 555 now'), (2, 4))
        self.assertEqual(cs.wer('', 'something'), (1, 0))
