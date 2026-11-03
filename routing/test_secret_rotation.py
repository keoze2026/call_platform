"""CH-473: changing the shared secret between Server A and Server B without a
refused call. B accepts the previous secret during the change; the tool
moves each server step by step and never prints the secret."""
import io
import os
import shutil
import sys
import tempfile
import time
from contextlib import redirect_stdout
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import Client, SimpleTestCase, override_settings

from routing.asterisk_handler import PREVIOUS_SECRET_USED_KEY

SCRIPTS = os.path.join(str(settings.BASE_DIR), 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import rotate_asterisk_secret as R  # noqa: E402

URL = '/api/twilio/asterisk/route/'
CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(ALLOWED_HOSTS=['*'], CACHES=CACHE, ASTERISK_SHARED_SECRET='new-one',
                   ASTERISK_SHARED_SECRET_PREVIOUS='old-one')
class BAcceptsBothTests(SimpleTestCase):
    def code(self, secret):
        h = {'HTTP_X_ASTERISK_SECRET': secret} if secret is not None else {}
        return Client().post(URL, data='{}', content_type='application/json', **h).status_code

    def setUp(self):
        cache.clear()

    def test_new_and_old_accepted_and_old_use_recorded(self):
        self.assertEqual(self.code('new-one'), 400)          # accepted: refused only for the empty body
        self.assertIsNone(cache.get(PREVIOUS_SECRET_USED_KEY))
        self.assertEqual(self.code('old-one'), 400)
        self.assertAlmostEqual(cache.get(PREVIOUS_SECRET_USED_KEY), time.time(), delta=5)

    def test_others_refused(self):
        for s in ('', 'wrong', None):
            self.assertEqual(self.code(s), 403)

    @override_settings(ASTERISK_SHARED_SECRET_PREVIOUS='')
    def test_outside_a_change_only_the_secret(self):
        self.assertEqual(self.code('old-one'), 403)
        self.assertEqual(self.code('new-one'), 400)


class HelperTests(SimpleTestCase):
    def test_new_secret_is_strong_and_env_safe(self):
        a, b = R.new_secret(), R.new_secret()
        self.assertNotEqual(a, b)
        self.assertRegex(a, R.GOOD)

    def test_env_edit_keeps_everything_else(self):
        text = '# comment\nA=1\nASTERISK_SHARED_SECRET="old"\n\nB=2\n'
        self.assertEqual(R.env_get(text, 'ASTERISK_SHARED_SECRET'), 'old')
        out = R.env_set(text, {'ASTERISK_SHARED_SECRET': 'new', 'ASTERISK_SHARED_SECRET_PREVIOUS': 'old'})
        self.assertEqual(out, '# comment\nA=1\nASTERISK_SHARED_SECRET=new\n\nB=2\nASTERISK_SHARED_SECRET_PREVIOUS=old\n')
        self.assertEqual(R.env_set(out, {'ASTERISK_SHARED_SECRET_PREVIOUS': None}),
                         '# comment\nA=1\nASTERISK_SHARED_SECRET=new\n\nB=2\n')

    def test_kamailio_secret_and_held_answer_address(self):
        cfg = ('#!substdef "!PUBLIC_IP!13.140.236.148!g"\n'
               '#!substdef "!HELD_URL!http://127.0.0.1:8000/api/twilio/asterisk/held-answer/!g"\n'
               '#!substdef "!SECRET!old-one!g"\nX-Asterisk-Secret: SECRET\n')
        out, notes = R.kamailio_set(cfg, 'new-one')
        self.assertIn('#!substdef "!SECRET!new-one!g"', out)
        self.assertNotIn('old-one', out)
        self.assertIn('!HELD_URL!http://10.20.0.2:8001/api/twilio/asterisk/held-answer/!g', out)
        self.assertIn('PUBLIC_IP!13.140.236.148', out)
        self.assertTrue(notes and '(Server B)' in notes[0])
        out2, notes2 = R.kamailio_set(out, 'third')
        self.assertIn('!HELD_URL!http://10.20.0.2:8001', out2)          # already right: left alone
        self.assertNotIn('->', notes2[0])
        with self.assertRaises(R.Refused):
            R.kamailio_set('no secret line here\n', 'x')


class StepTests(SimpleTestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.env = os.path.join(self.d, '.env')
        with open(self.env, 'w') as f:
            f.write('ALLOWED_HOSTS=x\nASTERISK_SHARED_SECRET=old-secret-0123456789abcdefghijklmnop\n')
        os.chmod(self.env, 0o600)
        self.new_file = os.path.join(self.d, 'asterisk_secret.new')
        for name, value in (('ENV', self.env), ('NEW_FILE', self.new_file),
                            ('SECRET_FILE', os.path.join(self.d, 'call_ended.secret')),
                            ('KAMAILIO_CFG', os.path.join(self.d, 'kamailio.cfg')),
                            ('backup', lambda p: p + '.bak')):
            p = mock.patch.object(R, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_step(self, fn, *a):
        out = io.StringIO()
        with redirect_stdout(out):
            fn(*a)
        return out.getvalue()

    def test_full_change(self):
        # Server B
        with mock.patch.object(R, 'recreate_web') as recreate:
            out = self.run_step(R.stage)
        recreate.assert_called_once()
        env = open(self.env).read()
        new = R.env_get(env, R.KEY)
        self.assertRegex(new, R.GOOD)
        self.assertEqual(R.env_get(env, R.PREVIOUS), 'old-secret-0123456789abcdefghijklmnop')
        self.assertEqual(open(self.new_file).read().strip(), new)
        self.assertEqual(os.stat(self.new_file).st_mode & 0o777, 0o600)
        self.assertNotIn(new, out)                               # never printed
        with self.assertRaises(R.Refused):
            R.stage()                                            # one change at a time

        # Server A (same files stand in for A's)
        with open(R.SECRET_FILE, 'w') as f:
            f.write('old-secret-0123456789abcdefghijklmnop\n')
        with open(R.KAMAILIO_CFG, 'w') as f:
            f.write('#!substdef "!SECRET!old-secret-0123456789abcdefghijklmnop!g"\n')
        with mock.patch.object(R, 'b_accepts', return_value=True):
            out = self.run_step(R.apply, self.new_file)
        self.assertEqual(open(R.SECRET_FILE).read().strip(), new)
        self.assertFalse(os.path.exists(self.new_file))
        self.assertNotIn(new, out)

        def sh(cmd, check=True, **kw):
            return {'asterisk -rx "core show channels count"': '3 active calls\n'}.get(cmd, '') \
                if isinstance(cmd, str) else ''
        with mock.patch.object(R, 'sh', side_effect=sh) as fake:
            out = self.run_step(R.kamailio)
        self.assertIn(new, open(R.KAMAILIO_CFG).read())
        self.assertIn('NOT restarted', out)
        self.assertNotIn('systemctl restart kamailio', [c.args[0] for c in fake.call_args_list])

        # Server B again: refused while the old secret was used recently
        with mock.patch.object(R, 'previous_last_used', return_value=time.time() - 60):
            with self.assertRaises(R.Refused):
                R.finish()
        with mock.patch.object(R, 'previous_last_used', return_value=time.time() - 3600), \
                mock.patch.object(R, 'recreate_web'):
            self.run_step(R.finish)
        env = open(self.env).read()
        self.assertEqual(R.env_get(env, R.KEY), new)
        self.assertNotIn(R.PREVIOUS, env)

    def test_apply_refuses_a_secret_b_does_not_take(self):
        with open(self.new_file, 'w') as f:
            f.write(R.new_secret())
        with mock.patch.object(R, 'b_accepts', return_value=False):
            with self.assertRaises(R.Refused):
                R.apply(self.new_file)
        with open(self.new_file, 'w') as f:
            f.write('short')
        with self.assertRaises(R.Refused):
            R.apply(self.new_file)

    def test_each_step_on_its_own_server(self):
        with mock.patch.object(R.os, 'geteuid', return_value=0), \
                mock.patch.object(R, 'which_server', return_value='A'):
            for step in ('stage', 'finish'):
                with self.assertRaises(R.Refused):
                    R.main(['x', step])
        with mock.patch.object(R.os, 'geteuid', return_value=0), \
                mock.patch.object(R, 'which_server', return_value='B'):
            for step in (['x', 'apply', '/tmp/f'], ['x', 'kamailio']):
                with self.assertRaises(R.Refused):
                    R.main(step)
