"""CH-327: scripts/host_watch.py - the server watching itself.

Run:  python manage.py test support.test_host_watch
"""
import importlib.util
import json
import os
import stat
import tempfile
import time
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase


def load():
    path = os.path.join(str(settings.BASE_DIR), 'scripts', 'host_watch.py')
    spec = importlib.util.spec_from_file_location('host_watch_t', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class HostWatchTests(SimpleTestCase):
    def setUp(self):
        self.hw = load()
        self.tmp = tempfile.mkdtemp()
        self.hw.STATE = os.path.join(self.tmp, 'state.json')
        self.hw.LOCK = self.hw.STATE + '.lock'
        self.hw.ENV_FILE = os.path.join(self.tmp, '.env')
        self.sent = []
        self.telegram_ok = True

        def telegram(text):
            self.sent.append(text)
            return self.telegram_ok
        self.hw.telegram = telegram

    def env(self, text):
        open(self.hw.ENV_FILE, 'w').write(text)

    def run_checks(self, *checks):
        return self.hw.run(checks=list(checks))

    def test_first_run_only_takes_the_picture(self):
        self.env('A=1\n')
        self.run_checks(self.hw.check_env)
        self.assertEqual(self.sent, [])
        mode = stat.S_IMODE(os.stat(self.hw.STATE).st_mode)
        self.assertEqual(mode, 0o600)

    def test_env_change_names_the_key_never_the_value(self):
        self.env('TELEGRAM_BOT_TOKEN=old-secret\nKEEP=1\n')
        self.run_checks(self.hw.check_env)
        self.env('TELEGRAM_BOT_TOKEN=new-secret\nKEEP=1\nNEW_KEY=x\n')
        rep = self.run_checks(self.hw.check_env)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('TELEGRAM_BOT_TOKEN (changed)', self.sent[0])
        self.assertIn('NEW_KEY (added)', self.sent[0])
        self.assertNotIn('secret', self.sent[0])
        self.assertNotIn('secret', open(self.hw.STATE).read())
        self.assertEqual(rep['events_24h']['env'], 1)
        self.assertEqual(rep['env_changes_24h'], 2)

    def test_a_log_emptied_in_place_is_cleared_but_rotation_is_not(self):
        log = os.path.join(self.tmp, 'auth.log')
        open(log, 'w').write('x' * 5000)
        self.hw.WATCHED_LOGS = [log]
        self.run_checks(self.hw.check_logs)
        open(log, 'w').close()                         # same file, now empty
        self.run_checks(self.hw.check_logs)
        self.assertIn('Log cleared', self.sent[-1])
        self.assertIn('🔴', self.sent[-1])
        # logrotate copytruncate: a fresh .1 next to it
        open(log, 'w').write('y' * 5000)
        self.run_checks(self.hw.check_logs)
        n = len(self.sent)
        open(log + '.1', 'w').write('y' * 5000)
        open(log, 'w').close()
        self.run_checks(self.hw.check_logs)
        self.assertEqual(len(self.sent), n)

    def test_a_new_port_is_red_once(self):
        listen = ('tcp LISTEN 0 511 0.0.0.0:443 0.0.0.0:* users:(("nginx",pid=1,fd=6))\n'
                  'udp UNCONN 0 0 0.0.0.0:14000 0.0.0.0:* users:(("asterisk",pid=2,fd=9))\n')
        with mock.patch.object(self.hw, 'sh', return_value=listen):
            self.run_checks(self.hw.check_ports)
        more = listen + 'tcp LISTEN 0 5 0.0.0.0:3306 0.0.0.0:* users:(("mysqld",pid=3,fd=6))\n'
        with mock.patch.object(self.hw, 'sh', return_value=more):
            rep = self.run_checks(self.hw.check_ports)
            self.run_checks(self.hw.check_ports)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('New port opened</b>: tcp/3306 by mysqld', self.sent[0])
        self.assertEqual(rep['ports']['new'], ['tcp/3306'])

    def test_ssh_login_from_a_new_ip_is_red(self):
        now = int(time.time())
        old = f'{now - 86400 * 3}.1 vmi sshd[1]: Accepted password for root from 41.90.1.1 port 5 ssh2\n'
        with mock.patch.object(self.hw, '_journal', return_value=old), \
                mock.patch.object(self.hw, 'sh', return_value=''):
            self.run_checks(self.hw.check_ssh)
        new = (f'{now}.2 vmi sshd[2]: Accepted publickey for root from 41.90.1.1 port 6 ssh2\n'
               f'{now}.3 vmi sshd[3]: Accepted password for root from 185.1.2.3 port 7 ssh2\n')
        with mock.patch.object(self.hw, '_journal', return_value=new), \
                mock.patch.object(self.hw, 'sh', return_value=''), \
                mock.patch.object(self.hw, 'country', return_value='(XX)'):
            rep = self.run_checks(self.hw.check_ssh)
            self.run_checks(self.hw.check_ssh)            # the same lines again: not twice
        self.assertEqual(len(self.sent), 1)
        self.assertIn('🔐 <b>SSH login</b>: root · 41.90.1.1', self.sent[0])
        self.assertIn('🔴 <b>SSH login</b>: root · 185.1.2.3 (XX)', self.sent[0])
        self.assertIn('new IP', self.sent[0])
        self.assertEqual(rep['red_by_kind_24h']['ssh'], 1)

    def test_alerts_wait_when_telegram_is_down(self):
        self.env('A=1\n')
        self.run_checks(self.hw.check_env)
        self.env('A=2\n')
        self.telegram_ok = False
        self.run_checks(self.hw.check_env)
        self.telegram_ok = True
        self.run_checks(self.hw.check_env)
        self.assertEqual(len(self.sent), 2)
        self.assertIn('A (changed)', self.sent[1])

    def test_a_look_sends_and_saves_nothing(self):
        self.env('A=1\n')
        self.run_checks(self.hw.check_env)
        self.env('A=2\n')
        self.hw.run(send=False, save=False, checks=[self.hw.check_env])
        self.run_checks(self.hw.check_env)
        self.assertEqual(len(self.sent), 1)               # the real run still saw it

    def test_a_dump_that_is_not_the_backup_is_red(self):
        self.run_checks(lambda r: None)                  # first run: the picture
        with mock.patch.object(self.hw, 'sh', return_value='pg_dump -U call_platform -d call_platform\n'):
            self.run_checks(self.hw.check_dumps)
            self.run_checks(self.hw.check_dumps)          # still running: not again
        self.assertEqual(len(self.sent), 1)
        self.assertIn('Database export / dump running', self.sent[0])
        with mock.patch.object(self.hw, 'sh', return_value='python3 /opt/call_platform/scripts/db_backup_b2.py\n'
                                                           'pg_dump -U call_platform\n'):
            self.run_checks(self.hw.check_dumps)          # the nightly backup
        self.assertEqual(len(self.sent), 1)

    def test_message_puts_red_first_and_stays_short(self):
        events = [('cron', False, 'cron line')] + [('port', True, f'red {i}') for i in range(30)]
        text = self.hw.message(events)
        self.assertTrue(text.startswith('🔴 <b>Avortyx server alert</b>\nred 0'))
        self.assertIn('… and 6 more', text)
