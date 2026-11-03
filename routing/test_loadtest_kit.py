"""CH-468: the load test uses SIPp 3.7.7, not Ubuntu's 3.7.2, whose audio
playback leaks memory on every call and crashes the test callers."""
import os
import re
import subprocess

from django.test import SimpleTestCase

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(BASE, 'scripts/loadtest/install_sipp.sh')


class InstallSippTests(SimpleTestCase):

    def read(self):
        with open(SCRIPT) as f:
            return f.read()

    def test_valid_bash(self):
        subprocess.run(['bash', '-n', SCRIPT], check=True)

    def test_version_has_the_pcap_fix(self):
        """3.7.3 fixed 'crash when using PCAP play with more than one call'."""
        m = re.search(r'^VERSION=v(\d+)\.(\d+)\.(\d+)$', self.read(), re.M)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(tuple(map(int, m.groups())), (3, 7, 3))

    def test_builds_with_audio_and_leaves_ubuntu_sipp(self):
        s = self.read()
        self.assertIn('-DUSE_PCAP=1', s)
        self.assertIn('DEST=/usr/local/bin/sipp', s)
        self.assertNotIn('apt-get remove', s)
        self.assertNotIn('/usr/bin/sipp"', s)

    def test_version_check_survives_sipp_exit_code(self):
        """`sipp -v` exits with 99: the check must not run it in a pipeline
        whose status set -o pipefail would turn into a failure."""
        s = self.read()
        self.assertIn('set -euo pipefail', s)
        self.assertIn("grep -o 'SIPp v[^ ]*' || true", s)
        self.assertNotRegex(s, r'"\$DEST" -v 2>&1 \| grep -q')


# ── CH-469: the load test runs on a test copy, never on live data ────────────

import contextlib
import http.server
import io
import json
import shutil
import sys
import tempfile
import threading
from unittest import mock

from django.conf import settings
from django.test import TestCase

LOADTEST = os.path.join(BASE, 'scripts', 'loadtest')
if LOADTEST not in sys.path:
    sys.path.insert(0, LOADTEST)
import make_env_b  # noqa: E402

LIVE_ENV = '''# live
SECRET_KEY="live-secret-key"
ASTERISK_SHARED_SECRET=shared-123
ALLOWED_HOSTS=avortyx.io,10.20.0.2
DATABASE_URL=postgresql://call_platform:pw@postgres:5432/call_platform
REDIS_URL=redis://:rpw@redis:6379/0
TELEGRAM_BOT_TOKEN='123:abc'
EMAIL_HOST_PASSWORD=mailpw   # inline note
REVO_API_KEY=revo
CALL_WORKERS=12
'''


class MakeEnvTests(SimpleTestCase):
    """The test routing's settings reach only test data and no outside service."""

    def build(self, text=LIVE_ENV):
        return make_env_b.build(make_env_b.parse(text))

    def test_test_database_and_redis(self):
        env = self.build()
        self.assertEqual(env['DATABASE_URL'], 'postgresql://call_platform:pw@postgres:5432/avortyx_loadtest')
        self.assertEqual(env['REDIS_URL'], 'redis://:rpw@redis:6379/9')

    def test_outside_services_off_and_written_even_when_empty(self):
        env = self.build()
        for key in ('TELEGRAM_BOT_TOKEN', 'EMAIL_HOST_PASSWORD', 'REVO_API_KEY', 'TWILIO_AUTH_TOKEN',
                    'IPQS_API_KEY', 'REALVALIDITO_API_KEY', 'SENTRY_DSN', 'ASTERISK_AMI_SECRET'):
            self.assertIn(key, env)          # explicit: the app would read a missing one from .env
            self.assertEqual(env[key], '')
        self.assertEqual(env['IPQS_ENABLED'], 'False')
        self.assertEqual(env['ASTERISK_AMI_HOST'], '127.0.0.1')

    def test_routing_settings_kept(self):
        env = self.build()
        self.assertEqual(env['SECRET_KEY'], 'live-secret-key')        # quotes removed
        self.assertEqual(env['ASTERISK_SHARED_SECRET'], 'shared-123')
        self.assertEqual(env['ALLOWED_HOSTS'], 'avortyx.io,10.20.0.2')
        self.assertEqual(env['CALL_WORKERS'], '12')

    def test_refusals(self):
        with self.assertRaises(SystemExit):
            self.build('DATABASE_URL=sqlite:///db.sqlite3\n')
        with self.assertRaises(SystemExit):
            self.build('DATABASE_URL=postgresql://u:p@h/avortyx_loadtest\n')
        with self.assertRaises(SystemExit):
            self.build('DATABASE_URL=postgresql://u:p@h/call_platform\nREDIS_URL=redis://redis:6379/9\n')
        with self.assertRaises(SystemExit):
            self.build('REDIS_URL=redis://redis:6379/0\n')

    def test_file_readable_by_root_only(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        src, dst = os.path.join(d, '.env'), os.path.join(d, '.env.loadtest')
        with open(src, 'w') as f:
            f.write(LIVE_ENV)
        with contextlib.redirect_stdout(io.StringIO()):
            make_env_b.main(src, dst)
        self.assertEqual(os.stat(dst).st_mode & 0o777, 0o600)
        with open(dst) as f:
            lines = f.read().splitlines()
        self.assertIn('DATABASE_URL=postgresql://call_platform:pw@postgres:5432/avortyx_loadtest', lines)
        self.assertIn('TELEGRAM_BOT_TOKEN=', lines)


def run_script(name, **env):
    out = io.StringIO()
    with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
        with open(os.path.join(LOADTEST, name)) as f:
            exec(compile(f.read(), name, 'exec'), {'__name__': '__main__'})
    return out.getvalue()


class ScriptsRefuseLiveTests(TestCase):
    def test_every_b_script_refuses_a_database_that_is_not_the_test_copy(self):
        for name in ('setup_b.py', 'report_b.py', 'during_b.py'):
            with self.assertRaises(SystemExit) as e:
                run_script(name, LOADTEST_STEP='report')
            self.assertIn('not the test database', str(e.exception), name)


class ScriptsOnTestCopyTests(TestCase):
    """setup, Sir's extras and the report, through the portal's own code."""

    def setUp(self):
        from accounts.models import Organization, User
        org = Organization.objects.create(name='Avortyx', slug='avx')
        User.objects.create_user(username='o@x.io', email='o@x.io', password='pw-123456',
                                 organization=org, is_superuser=True)
        p = mock.patch.dict(settings.DATABASES['default'], {'NAME': 'avortyx_loadtest'})
        p.start()
        self.addCleanup(p.stop)

    def test_setup_extras_and_report(self):
        from campaigns.models import Campaign
        from phone_numbers.models import PhoneNumber
        self.assertIn('LOADTEST ready', run_script('setup_b.py'))
        c = Campaign.objects.get(name='LOADTEST')

        out = run_script('during_b.py', LOADTEST_STEP='tfns')
        self.assertIn('added 1000 TFNs', out)
        self.assertEqual(PhoneNumber.objects.filter(campaign=c).count(), 1001)
        self.assertIn('added 0 TFNs', run_script('during_b.py', LOADTEST_STEP='tfns'))

        seen = []
        with mock.patch('time.sleep', lambda s: seen.append(Campaign.objects.get(pk=c.pk).status)):
            out = run_script('during_b.py', LOADTEST_STEP='pause')
        self.assertEqual(seen, ['paused'])
        self.assertEqual(Campaign.objects.get(pk=c.pk).status, 'active')
        self.assertIn('calls that arrived while paused: 0', out)

        out = run_script('during_b.py', LOADTEST_STEP='report')
        for part in ('summary', 'by day', 'campaigns', 'buyers', 'publishers', 'call list', 'all'):
            self.assertIn(part, out)

        out = run_script('report_b.py')
        self.assertIn('LOADTEST calls on B: 0', out)
        self.assertIn('LT-A: most calls at once 0 (limit 400) -> OK', out)


class Recorder(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        self.server.seen.append((self.path, json.loads(body)))
        reply = {'action': 'dial', 'buyer_number': '+19995550001', 'call_log_id': 'test-id',
                 'max_duration': 1800, 'recording_enabled': True}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(reply).encode())

    def log_message(self, *a):
        pass


def server():
    srv = http.server.HTTPServer(('127.0.0.1', 0), Recorder)
    srv.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}'


class ServerAWrapperTests(SimpleTestCase):
    """Server A's test routing script and hang-up report go to the TEST
    routing and the test folders, even with the live settings in the
    environment."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.test, self.test_url = server()
        self.live, self.live_url = server()
        self.addCleanup(self.test.shutdown)
        self.addCleanup(self.live.shutdown)
        self.secret = os.path.join(self.dir, 'secret')
        with open(self.secret, 'w') as f:
            f.write('s3\n')

    def test_routing_goes_to_the_test_copy(self):
        state = os.path.join(self.dir, 'state')
        live_state = os.path.join(self.dir, 'live-state')
        env = dict(os.environ, LOADTEST_B_URL=self.test_url, LOADTEST_STATE=state,
                   LOADTEST_LIVE_AGI=os.path.join(BASE, 'scripts', 'fallback', 'route_call.py'),
                   AVX_ROUTE_BASE=self.live_url, AVX_FALLBACK_DIR=live_state,      # live, ignored
                   AVX_FALLBACK_CODE=os.path.join(BASE, 'scripts', 'fallback'), AVX_SECRET_FILE=self.secret)
        stdin = ('agi_callerid: 15551234567\nagi_extension: 19995550100\nagi_uniqueid: 1793.7\n\n'
                 + '200 result=1\n' * 30)
        out = subprocess.run([sys.executable, os.path.join(LOADTEST, 'route_call_loadtest.py')],
                             input=stdin, capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('SET VARIABLE CALL_LOG_ID "test-id"', out.stdout)
        self.assertEqual([p for p, _ in self.test.seen], ['/api/twilio/asterisk/route/'])
        self.assertEqual(self.live.seen, [])
        self.assertFalse(os.path.exists(live_state))

    def test_wrapper_replaces_the_live_settings(self):
        probe = os.path.join(self.dir, 'probe.py')
        with open(probe, 'w') as f:
            f.write("import os; print(os.environ['AVX_ROUTE_BASE'], os.environ['AVX_FALLBACK_DIR'])\n")
        env = dict(os.environ, LOADTEST_LIVE_AGI=probe, AVX_ROUTE_BASE='http://10.20.0.2:8001',
                   AVX_FALLBACK_DIR='/var/lib/avortyx/fallback')
        env.pop('LOADTEST_B_URL', None)
        env.pop('LOADTEST_STATE', None)
        out = subprocess.run([sys.executable, os.path.join(LOADTEST, 'route_call_loadtest.py')],
                             capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(out.stdout.split(), ['http://10.20.0.2:8011', '/var/lib/avortyx/fallback-loadtest'])

    def ended(self, b_url, *args):
        live_ended = os.path.join(self.dir, 'call_ended.sh')     # installed with mode 755 on A
        shutil.copy(os.path.join(BASE, 'docs', 'asterisk', 'call_ended.sh'), live_ended)
        os.chmod(live_ended, 0o755)
        env = dict(os.environ, LOADTEST_B_URL=b_url,
                   LOADTEST_SPOOL=os.path.join(self.dir, 'spool'),
                   LOADTEST_STATE=os.path.join(self.dir, 'state'),
                   LOADTEST_LOG=os.path.join(self.dir, 'ended.log'),
                   LOADTEST_LIVE_ENDED=live_ended,
                   CALL_ENDED_URL=self.live_url + '/api/twilio/asterisk/call-ended/',   # live, ignored
                   CALL_ENDED_SPOOL=os.path.join(self.dir, 'live-spool'),
                   ASTERISK_SHARED_SECRET='s3', CALL_ENDED_ATTEMPTS='1')
        return subprocess.run(['bash', os.path.join(LOADTEST, 'call_ended_loadtest.sh'), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_hangup_report_goes_to_the_test_copy(self):
        out = self.ended(self.test_url, 'test-id', '90', 'ANSWER', 'recordings-loadtest/test-id.wav',
                         '+15551234567', '92')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(self.test.seen), 1)
        path, body = self.test.seen[0]
        self.assertEqual((path, body['call_log_id'], body['duration']),
                         ('/api/twilio/asterisk/call-ended/', 'test-id', 90))
        self.assertEqual(self.live.seen, [])

    def test_undelivered_report_waits_in_the_test_spool(self):
        import socket
        dead = socket.socket()
        dead.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{dead.getsockname()[1]}'
        dead.close()
        self.ended(url, 'test-id', '0', 'NOANSWER', '', '+15551234567', '5')
        self.assertEqual(len(os.listdir(os.path.join(self.dir, 'spool'))), 1)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'live-spool')))


class TestDialPlanTests(SimpleTestCase):
    def test_test_dial_plan_never_uses_the_live_pieces(self):
        with open(os.path.join(LOADTEST, 'extensions_loadtest.conf')) as f:
            plan = '\n'.join(l for l in f.read().splitlines() if not l.lstrip().startswith(';'))
        self.assertIn('AGI(route_call_loadtest.py)', plan)
        self.assertNotIn('AGI(route_call.py)', plan)
        self.assertNotIn('macro-call_end', plan)
        self.assertNotIn('call_ended.sh', plan.replace('call_ended_loadtest.sh', ''))
        self.assertNotIn('/var/spool/asterisk/recordings/', plan)
        self.assertNotIn('@trunk', plan)
