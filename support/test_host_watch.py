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
            self.run_checks(self.hw.check_dumps)          # the nightly backup: not alerted
        self.assertEqual(len(self.sent), 2)
        self.assertIn('Resolved', self.sent[1])           # the unknown dump is gone

    def test_message_puts_red_first_and_stays_short(self):
        events = [('cron', False, 'cron line')] + [('port', True, f'red {i}') for i in range(30)]
        text = self.hw.message(events)
        self.assertTrue(text.startswith('🔴 <b>Avortyx Server Alert</b> · <code>'))
        self.assertIn('\nred 0\n', text)
        self.assertIn('… and 6 more', text)

    def test_a_critical_condition_repeats_every_15_minutes_then_resolves(self):
        listen = 'tcp LISTEN 0 511 0.0.0.0:443 0.0.0.0:* users:(("nginx",pid=1,fd=6))\n'
        more = listen + 'tcp LISTEN 0 5 0.0.0.0:3306 0.0.0.0:* users:(("mysqld",pid=3,fd=6))\n'
        clock = [1_000_000.0]
        with mock.patch.object(self.hw.time, 'time', side_effect=lambda: clock[0]):
            with mock.patch.object(self.hw, 'sh', return_value=listen):
                self.run_checks(self.hw.check_ports)
            with mock.patch.object(self.hw, 'sh', return_value=more):
                self.run_checks(self.hw.check_ports)          # first alert
                clock[0] += 600
                self.run_checks(self.hw.check_ports)          # 10 min: quiet
                clock[0] += 400
                self.run_checks(self.hw.check_ports)          # 16 min: again
            with mock.patch.object(self.hw, 'sh', return_value=listen):
                self.run_checks(self.hw.check_ports)          # closed: resolved
        self.assertEqual(len(self.sent), 3)
        self.assertIn('New port opened', self.sent[0])
        self.assertIn('🔁 Still not resolved (16 min)', self.sent[1])
        self.assertIn('✅ Resolved', self.sent[2])

    def test_accept_stops_the_repeats_and_approves_the_ports(self):
        more = ('tcp LISTEN 0 511 0.0.0.0:443 0.0.0.0:* users:(("nginx",pid=1,fd=6))\n'
                'tcp LISTEN 0 5 0.0.0.0:3306 0.0.0.0:* users:(("mysqld",pid=3,fd=6))\n')
        with mock.patch.object(self.hw, 'sh', return_value=more):
            self.run_checks(self.hw.check_ports)
            self.hw.accept()
            rep = self.run_checks(self.hw.check_ports)
        self.assertEqual(rep['ports']['new'], [])
        self.assertEqual(self.sent, [])

    def test_short_lived_helpers_never_count_as_unknown_processes(self):
        ps = ('3131068 root 150.0 0 runc:[2:INIT]\n'
              '3131054 root 50.0 1 runc\n'
              '3141708 root 300.0 0 ps\n'
              '4000000 root 99.0 30 xmrig\n')             # hot but only 30 s old
        with mock.patch.object(self.hw, 'sh', return_value=ps):
            self.run_checks(self.hw.check_processes)
            self.run_checks(self.hw.check_processes)
        self.assertEqual(self.sent, [])

    def test_an_unknown_process_busy_for_minutes_is_red(self):
        ps = '4000000 root 99.0 600 xmrig\n'
        with mock.patch.object(self.hw, 'sh', return_value=ps):
            self.run_checks(self.hw.check_processes)
            self.run_checks(self.hw.check_processes)
            self.run_checks(self.hw.check_processes)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('Unknown process running</b>: xmrig · 99% CPU · user root · pid 4000000 · running 10 min',
                      self.sent[0])


class FirewallAlertTests(SimpleTestCase):
    """CH-351: Fail2Ban bans read the way Sir laid them out."""

    def setUp(self):
        self.hw = load()

    BAN = '195.178.110.232            REJECT      Anywhere                   # by Fail2Ban after 3 attempts against sshd'
    OLD = '201.139.186.238            REJECT      Anywhere                   # by Fail2Ban after 3 attempts against sshd'

    def test_a_ban_and_an_unban(self):
        text = self.hw.firewall_text(['Anywhere REJECT 195.178.110.232 # by Fail2Ban after 3 attempts against sshd'],
                                     ['Anywhere REJECT 201.139.186.238 # by Fail2Ban after 3 attempts against sshd'],
                                     '09:31 UTC')
        self.assertEqual(text, '<b>Firewall rules updated</b> · 09:31 UTC\n\n'
                               '• Blocked by Fail2Ban: <code>195.178.110.232</code>\n'
                               '• Ban removed: <code>201.139.186.238</code>\n\n'
                               'Both actions followed 3 failed attempts.')

    def test_one_ban_with_ufw_padding(self):
        text = self.hw.firewall_text([self.BAN], [], '10:02 UTC')
        self.assertIn('• Blocked by Fail2Ban: <code>195.178.110.232</code>', text)
        self.assertIn('This action followed 3 failed attempts.', text)

    def test_a_rule_changed_by_hand_is_still_shown(self):
        text = self.hw.firewall_text(['5080/udp ALLOW 45.79.4.41'], [], '10:05 UTC')
        self.assertIn('• Rule added: <code>5080/udp ALLOW 45.79.4.41</code>', text)
        self.assertIn('changed by hand', text)
        self.assertNotIn('Fail2Ban', text)

    def test_the_whole_alert(self):
        body = self.hw.firewall_text([self.BAN], [self.OLD], '09:31 UTC')
        msg = self.hw.message([('firewall', True, body)])
        self.assertTrue(msg.startswith('🔴 <b>Avortyx Server Alert</b> · <code>'))
        self.assertIn('</code>\n\n<b>Firewall rules updated</b> · 09:31 UTC', msg)

    def test_the_watch_sends_it(self):
        import os, tempfile
        tmp = tempfile.mkdtemp()
        self.hw.STATE = os.path.join(tmp, 'state.json')
        self.hw.LOCK = self.hw.STATE + '.lock'
        sent = []
        self.hw.telegram = lambda text: sent.append(text) or True
        status = 'Status: active\n\nTo   Action   From\n--   ------   ----\n'
        with mock.patch.object(self.hw, 'sh', return_value=status + self.OLD + '\n'):
            self.hw.run(checks=[self.hw.check_firewall])
        with mock.patch.object(self.hw, 'sh', return_value=status + self.BAN + '\n'):
            self.hw.run(checks=[self.hw.check_firewall])
        self.assertEqual(len(sent), 1)
        self.assertIn('• Blocked by Fail2Ban: <code>195.178.110.232</code>', sent[0])
        self.assertIn('• Ban removed: <code>201.139.186.238</code>', sent[0])
        self.assertIn('Both actions followed 3 failed attempts.', sent[0])
