"""CH-470: each server's settings are packed daily and copied to B2."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from unittest import mock

from django.test import SimpleTestCase

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import config_backup_b2 as C  # noqa: E402


class ConfigBackupTests(SimpleTestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        etc = os.path.join(self.d, 'etc', 'asterisk')
        os.makedirs(etc)
        with open(os.path.join(etc, 'pjsip.conf'), 'w') as f:
            f.write('[carrier]\n')
        os.symlink('pjsip.conf', os.path.join(etc, 'link.conf'))
        os.mkfifo(os.path.join(etc, 'pipe'))
        self.env = os.path.join(self.d, 'app.env')
        with open(self.env, 'w') as f:
            f.write('B2_KEY_ID=k\n')
        root = os.path.join(self.d, 'root')
        os.makedirs(os.path.join(root, 'sub'))
        for name, size in (('late_answers.py', 10), ('db-1.sql.gz', 10), ('huge.txt', C.ROOT_FILE_MAX + 1)):
            with open(os.path.join(root, name), 'wb') as f:
                f.write(b'x' * size)
        self.astdb = os.path.join(self.d, 'astdb.sqlite3')
        with sqlite3.connect(self.astdb) as db:
            db.execute('CREATE TABLE astdb (key TEXT, value TEXT)')
            db.execute("INSERT INTO astdb VALUES ('/avortyx/cancel_hold', '1')")
        out = os.path.join(self.d, 'out')
        for name, value in (('PATHS', [etc, self.env, os.path.join(self.d, 'missing')]),
                            ('ROOT_HOME', root), ('ASTDB', self.astdb), ('OUT', out),
                            ('STATE', os.path.join(self.d, 'state', 'last.json')),
                            ('INFO', [('ok', ['echo', 'asterisk 20.6']), ('broken', ['false'])])):
            p = mock.patch.object(C, name, value)
            p.start()
            self.addCleanup(p.stop)

    def names(self, path):
        with tarfile.open(path) as t:
            return {m.name: m for m in t.getmembers()}

    def test_archive_holds_the_settings_and_skips_the_rest(self):
        path = os.path.join(self.d, 'a.tar.gz')
        count, skipped = C.build(path)
        m = self.names(path)
        rel = lambda p: p.lstrip('/')  # noqa: E731
        self.assertIn(rel(os.path.join(self.d, 'etc/asterisk/pjsip.conf')), m)
        self.assertTrue(m[rel(os.path.join(self.d, 'etc/asterisk/link.conf'))].issym())
        self.assertNotIn(rel(os.path.join(self.d, 'etc/asterisk/pipe')), m)
        self.assertIn(rel(self.env), m)
        self.assertIn(rel(os.path.join(self.d, 'root/late_answers.py')), m)
        self.assertNotIn(rel(os.path.join(self.d, 'root/db-1.sql.gz')), m)
        self.assertNotIn(rel(os.path.join(self.d, 'root/huge.txt')), m)
        self.assertEqual(skipped, [os.path.join(self.d, 'missing')])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(path + '.part'))
        with tarfile.open(path) as t:
            info = t.extractfile('rebuild-info.txt').read().decode()
            self.assertIn('asterisk 20.6', info)
            self.assertIn('===== broken =====\n(exit 1)', info)
            db = os.path.join(self.d, 'restored.sqlite3')
            with open(db, 'wb') as f:
                f.write(t.extractfile(rel(self.astdb)).read())
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT value FROM astdb').fetchone(), ('1',))

    def b2(self, match=True):
        b2 = mock.Mock()

        def upload(path, name, sha1, size, *a, **k):
            b2.uploaded = name
            return {'contentLength': size, 'contentSha1': sha1 if match else 'other'}
        b2.upload.side_effect = upload
        return b2

    def test_backup_uploaded_and_recorded(self):
        b2 = self.b2()
        with mock.patch.object(C, 'b2_client', return_value=b2), mock.patch('sys.stdout'):
            self.assertEqual(C.main([]), 0)
        self.assertTrue(b2.uploaded.startswith('backups/config/'))
        with open(C.STATE) as f:
            state = json.load(f)
        self.assertTrue(state['last_ok']['ok'])
        self.assertEqual(state['last_ok']['b2'], b2.uploaded)
        self.assertEqual(len(os.listdir(C.OUT)), 1)

    def test_a_bad_upload_is_recorded_as_failed(self):
        with mock.patch.object(C, 'b2_client', return_value=self.b2(match=False)), mock.patch('sys.stdout'):
            self.assertEqual(C.main([]), 1)
        with open(C.STATE) as f:
            state = json.load(f)
        self.assertFalse(state['last_run']['ok'])
        self.assertNotIn('last_ok', state)
        self.assertIn('does not match', state['last_run']['error'])

    def test_keeps_the_last_14(self):
        os.makedirs(C.OUT)
        for i in range(20):
            open(os.path.join(C.OUT, f'avx-calls-202611{i:02d}-0440.tar.gz'), 'w').close()
        C.prune()
        left = sorted(os.listdir(C.OUT))
        self.assertEqual(len(left), 14)
        self.assertEqual(left[0], 'avx-calls-20261106-0440.tar.gz')


class WatchdogBackupCheckTests(SimpleTestCase):
    """The watchdog warns when the newest good settings backup is over 36 h old."""

    def check(self, state):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        st = os.path.join(d, 'last.json')
        if state is not None:
            with open(st, 'w') as f:
                json.dump(state, f)
        script = ('alert() { echo "ALERT $1"; }; clear_alert() { echo "CLEAR $1"; }; SERVER_NAME="call server"\n'
                  f'CONFIG_BACKUP_STATE={st}\n'
                  + subprocess.run(['sed', '-n', '/^check_config_backup() {/,/^}/p',
                                    os.path.join(BASE, 'deploy', 'watchdog.sh')],
                                   capture_output=True, text=True).stdout
                  + '\ncheck_config_backup\n')
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True).stdout.strip()

    def test_overdue_fresh_and_never_run(self):
        import time
        now = int(time.time())
        self.assertEqual(self.check({'last_ok': {'at': now - 37 * 3600}}), 'ALERT config_backup')
        self.assertEqual(self.check({'last_run': {'ok': False, 'at': now}}), 'ALERT config_backup')
        self.assertEqual(self.check({'last_ok': {'at': now - 3600}}), 'CLEAR config_backup')
        self.assertEqual(self.check(None), '')
