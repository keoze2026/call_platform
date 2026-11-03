"""CH-472: the status bot sees both servers. Server A posts its own health
and security report to Server B every minute; the bot shows the phone system
from A and each server's own health and security."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from unittest import mock

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings

from support import system_status as S
from support.test_system_status import Ok, fake_host

SECRET = 'test-secret'


def server_report(**over):
    h = fake_host(**over)
    h['watch'] = {'server': {'hostname': 'x', 'label': 'Contabo VDS', 'ip': '1.2.3.4'},
                  'services': [{'label': 'SIP engine', 'ok': True}], 'ssh': {'root_login': 'prohibit-password',
                                                                              'password_login': 'no'},
                  'events_24h': {}, 'red_by_kind_24h': {}}
    return h


class ViewTests(SimpleTestCase):
    def test_one_server_unchanged(self):
        b = server_report()
        self.assertIs(S.view(b, {}), b)

    def test_two_servers(self):
        a = server_report(asterisk={'running': True, 'active_calls': 250})
        b = server_report(asterisk={'running': False}, backups={'last_ok': {'at': 1, 'ok': True}},
                          logs={'daily_status': 'sent at 04:05'})
        v = S.view(b, a)
        self.assertEqual(v['asterisk']['active_calls'], 250)          # the phone system: A
        self.assertEqual(v['backups'], {'last_ok': {'at': 1, 'ok': True}})   # the database backup: B
        self.assertEqual(v['logs']['daily_status'], 'sent at 04:05')
        self.assertEqual([n for (n, _), _ in v['_servers']], ['Call server', 'Portal server'])


@override_settings(ALLOWED_HOSTS=['*'], TELEGRAM_BOT_TOKEN='t', TELEGRAM_SUPPORT_CHAT_ID='-100777',
                   IPQS_API_KEY='k', REALVALIDITO_API_KEY='k', EMAIL_HOST_PASSWORD='',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class TwoServerStatusTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.b_file = os.path.join(self.tmp, 'host_status.json')
        self.a_file = os.path.join(self.tmp, 'host_status_a.json')
        for p in (mock.patch.object(S, 'PARALLEL', False),
                  mock.patch.object(S, 'HOST_FILE', self.b_file),
                  mock.patch.object(S, 'HOST_A_FILE', self.a_file),
                  mock.patch.object(S, 'SUBS_FILE', os.path.join(self.tmp, 'subs.json')),
                  mock.patch.object(S, 'ssl_expiry', return_value=date.today() + timedelta(days=60)),
                  mock.patch.object(S, 'domain_expiry', return_value=date.today() + timedelta(days=200)),
                  mock.patch('socket.getaddrinfo', return_value=[]),
                  mock.patch('requests.get', return_value=Ok()),
                  mock.patch('requests.head', return_value=Ok())):
            p.start()
            self.addCleanup(p.stop)
        self.write(self.b_file, server_report(asterisk={'running': False}))
        self.write(self.a_file, server_report(asterisk={'running': True, 'active_calls': 120, 'cancel_hold_on': False,
                                                        'trunk': [{'endpoint': 'carrier', 'status': 'Avail',
                                                                   'rtt_ms': '20'}], 'quality': {'channels': 0}}))

    def write(self, path, data):
        with open(path, 'w') as f:
            json.dump(data, f)

    def items(self):
        return {it.name: it for _, items in S.collect() for it in items}

    def test_each_server_shown_and_phone_system_from_a(self):
        got = self.items()
        for name in ('Call server · CPU', 'Portal server · CPU', 'Call server · Disk', 'Portal server · RAM',
                     'Call server · Services', 'Portal server · SSH logins (24h)', 'Call server · Firewall',
                     'Portal server · Firewall'):
            self.assertIn(name, got)
        self.assertNotIn('CPU', got)
        self.assertEqual(got['Concurrent calls'].detail.split(' ')[0], '120')   # Server A's Asterisk
        self.assertNotIn('Phone system (Asterisk)', got)                         # B has none: not "down"
        self.assertEqual(got['Database backup'].status, S.OK)                     # B's backup

    def test_a_silent_is_flagged(self):
        stale = server_report()
        stale['at'] = time.time() - 900
        self.write(self.a_file, stale)
        got = self.items()
        self.assertEqual(got['Call server · Server (host data)'].status, S.WARN)
        self.assertIn('no fresh host data', got['Carrier links'].detail)
        self.assertEqual(got['Portal server · CPU'].status, S.OK)

    def test_a_warning_names_the_server(self):
        a = json.load(open(self.a_file))
        a['system']['disk_used_pct'] = 95
        self.write(self.a_file, a)
        sent = []
        S.watch(sent.append)
        self.assertTrue(any('Call server · Disk' in m for m in sent), sent)
        self.assertFalse(any('Portal server · Disk' in m for m in sent))


@override_settings(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET)
class HostReportEndpointTests(SimpleTestCase):
    url = '/api/twilio/asterisk/host-status/'

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.file = os.path.join(self.tmp, 'run', 'host_status_a.json')
        p = mock.patch.object(S, 'HOST_A_FILE', self.file)
        p.start()
        self.addCleanup(p.stop)

    def post(self, body, secret=SECRET):
        return Client().post(self.url, data=body, content_type='application/json',
                             **({'HTTP_X_ASTERISK_SECRET': secret} if secret else {}))

    def test_kept_for_the_bot(self):
        r = self.post(json.dumps({'at': 1793700000, 'system': {'cpus': 16}}))
        self.assertEqual(r.status_code, 200)
        with open(self.file) as f:
            kept = json.load(f)
        self.assertEqual(kept['system'], {'cpus': 16})
        self.assertIn('received_at', kept)

    def test_refusals(self):
        self.assertEqual(self.post('{"at": 1}', secret=None).status_code, 403)
        self.assertEqual(self.post('not json').status_code, 400)
        self.assertEqual(self.post('{"system": {}}').status_code, 400)
        self.assertEqual(self.post('[1, 2]').status_code, 400)
        self.assertEqual(self.post(json.dumps({'at': 1, 'x': 'y' * (3 * 1024 * 1024)})).status_code, 413)
        self.assertFalse(os.path.exists(self.file))


SCRIPTS = os.path.join(str(settings.BASE_DIR), 'scripts')


class HostScriptsTests(SimpleTestCase):
    def run_py(self, code, **env):
        e = dict(os.environ, **env)
        e.pop('OUR_SIP_IPS', None) if 'OUR_SIP_IPS' not in env else None
        return subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=e,
                              cwd=SCRIPTS, timeout=60)

    def test_own_ip_not_the_old_servers(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, '.env'), 'w') as f:
            f.write('OUR_SIP_IPS=13.140.236.148\n')
        code = 'import host_watch as h; print(",".join(h.OUR_IPS))'
        self.assertEqual(self.run_py(code, OUR_SIP_IPS='9.9.9.9').stdout.strip(), '9.9.9.9')
        self.assertEqual(self.run_py(code, APP_DIR=d).stdout.strip(), '13.140.236.148')
        sys.path.insert(0, SCRIPTS)
        import host_watch
        with mock.patch.dict(os.environ, {'APP_DIR': tempfile.gettempdir()}), \
                mock.patch.object(host_watch, 'APP', tempfile.gettempdir()), \
                mock.patch.object(host_watch, 'sh', return_value='1.1.1.1 via 13.140.236.1 dev eth0 src 13.140.236.153 uid 0'):
            os.environ.pop('OUR_SIP_IPS', None)
            self.assertEqual(host_watch.our_ips(), ('13.140.236.153',))

    def test_a_posts_its_report_to_b(self):
        import http.server
        import threading
        seen = []

        class B(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, self.headers.get('X-Asterisk-Secret'),
                             json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(('127.0.0.1', 0), B)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, '.env'), 'w') as f:
            f.write('ASTERISK_SHARED_SECRET="s3cret"\n')
        code = ('import host_status as h; h.SECTIONS[:] = [h.system]; h.main()')
        out = self.run_py(code, APP_DIR=d, HOST_STATUS_FILE=os.path.join(d, 'run', 'host_status.json'),
                          HOST_STATUS_POST_URL=f'http://127.0.0.1:{srv.server_address[1]}/api/twilio/asterisk/host-status/',
                          OUR_SIP_IPS='13.140.236.148')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(seen), 1)
        path, secret, body = seen[0]
        self.assertEqual((path, secret), ('/api/twilio/asterisk/host-status/', 's3cret'))
        self.assertIn('cpus', body['system'])
        self.assertTrue(os.path.exists(os.path.join(d, 'run', 'host_status.json')))

    def test_b_not_answering_is_logged_not_fatal(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        code = 'import host_status as h; h.SECTIONS[:] = [h.system]; h.main()'
        out = self.run_py(code, APP_DIR=d, HOST_STATUS_FILE=os.path.join(d, 'host_status.json'),
                          HOST_STATUS_POST_URL='http://127.0.0.1:9/x', OUR_SIP_IPS='1.2.3.4')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('report not sent to Server B', out.stdout)
        self.assertTrue(os.path.exists(os.path.join(d, 'host_status.json')))
