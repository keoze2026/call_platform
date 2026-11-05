"""CH-604: the Non Priority topic - portal changes and the EOD report moved
there, deleted after 47 hours, and today's calls as an Excel file every 15
minutes.

Run:  python manage.py test support.test_nonpriority
"""
import importlib.util
import io
import json
import os
import tempfile
import time
import zipfile
from datetime import datetime, timezone as dt_timezone
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from support import nonpriority as N
from support import telegram_autodelete as A

GROUP = '-1001234567890'
HOUR = 3600
LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def sent(chat, mid):
    return json.dumps({'ok': True, 'result': {'message_id': mid, 'chat': {'id': int(chat)}}})


class EnvMixin:
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        for p in (mock.patch.object(A, 'LOG', os.path.join(self.tmp, 'sent.jsonl')),
                  mock.patch.object(A, 'ENV_FILE', os.path.join(self.tmp, '.env')),
                  mock.patch.dict(os.environ, {'TELEGRAM_AUTODELETE_DAYS': '', 'TELEGRAM_AUTODELETE_KEEP': '',
                                               'TELEGRAM_NONPRIORITY_THREAD_ID': '',
                                               'TELEGRAM_NONPRIORITY_DELETE_HOURS': '',
                                               'NONPRIORITY_CALL_BACKUP': '', 'EOD_TO_NONPRIORITY': ''})):
            p.start()
            self.addCleanup(p.stop)

    def env(self, **kv):
        with open(A.ENV_FILE, 'w') as f:
            f.write(''.join(f'{k}={v}\n' for k, v in kv.items()))


class DeleteAfter47HoursTests(EnvMixin, SimpleTestCase):
    def note(self, mid, age_hours, kind):
        with mock.patch('time.time', return_value=time.time() - age_hours * HOUR):
            self.assertTrue(A.remember(GROUP, sent(GROUP, mid), kind))

    def test_non_priority_goes_after_47_hours_the_rest_after_a_week(self):
        self.note(1, 48, 'nonpriority')
        self.note(2, 46, 'nonpriority')
        self.note(3, 48, 'status')
        gone = []
        A.prune('t', delete=lambda tok, chat, mid: gone.append(mid) or 'ok')
        self.assertEqual(gone, [1])
        self.assertEqual(sorted(e['id'] for e in A.entries()), [2, 3])

    def test_hours_from_env(self):
        self.env(TELEGRAM_NONPRIORITY_DELETE_HOURS='24')
        self.assertEqual(A.max_age('nonpriority'), 24 * HOUR)
        self.env(TELEGRAM_NONPRIORITY_DELETE_HOURS='0')
        self.assertEqual(A.max_age('nonpriority'), 7 * 86400)          # 0: the normal week

    def test_ssh_alerts_in_non_priority_are_noted_as_such(self):
        path = os.path.join(str(settings.BASE_DIR), 'scripts', 'host_watch.py')
        spec = importlib.util.spec_from_file_location('host_watch_np', path)
        hw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hw)
        hw._env = lambda name: {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_SUPPORT_CHAT_ID': GROUP,
                                'TELEGRAM_NOTIFY_THREAD_ID': '7'}.get(name, '')
        resp = mock.MagicMock(status=200)
        resp.__enter__.return_value = resp
        resp.read.return_value = sent(GROUP, 5).encode()
        with mock.patch('urllib.request.urlopen', return_value=resp):
            hw.telegram('ssh login', thread='9')
        resp.read.return_value = sent(GROUP, 6).encode()
        with mock.patch('urllib.request.urlopen', return_value=resp):
            hw.telegram('🔴 server down')
        self.assertEqual([(e['id'], e['kind']) for e in A.entries()], [(5, 'nonpriority'), (6, 'server')])


@override_settings(TELEGRAM_BOT_TOKEN='t', TELEGRAM_SUPPORT_CHAT_ID=GROUP, TELEGRAM_NOTIFY_THREAD_ID='7')
class RoutingTests(EnvMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        from support import system_status as S
        p = mock.patch.object(S, '_cfg', return_value='')
        p.start()
        self.addCleanup(p.stop)

    def test_without_the_topic_everything_stays_as_before(self):
        self.assertEqual(N.target(), (GROUP, '7', False))
        self.assertIsNone(N.eod_thread(GROUP))

    def test_portal_changes_go_to_non_priority_and_are_deleted_with_it(self):
        self.env(TELEGRAM_NONPRIORITY_THREAD_ID='9')
        answer = mock.Mock(ok=True, content=sent(GROUP, 42).encode())
        with mock.patch('requests.post', return_value=answer) as post, \
                mock.patch('support.system_status.portal_changes', side_effect=lambda send: send('🔀 x') or 1):
            from tasks import portal_change_alerts
            portal_change_alerts()
        self.assertEqual(post.call_args.kwargs['json']['message_thread_id'], '9')
        self.assertEqual([(e['id'], e['kind']) for e in A.entries()], [(42, 'nonpriority')])

    def test_eod_moves_unless_switched_off(self):
        self.env(TELEGRAM_NONPRIORITY_THREAD_ID='9')
        self.assertEqual(N.eod_thread(GROUP), ('9', 'nonpriority'))
        self.assertIsNone(N.eod_thread('-100999'))                       # not the support group
        self.env(TELEGRAM_NONPRIORITY_THREAD_ID='9', EOD_TO_NONPRIORITY='off')
        self.assertIsNone(N.eod_thread(GROUP))


@override_settings(TELEGRAM_BOT_TOKEN='t', TELEGRAM_SUPPORT_CHAT_ID=GROUP, TELEGRAM_NOTIFY_THREAD_ID='7',
                   MAIN_ORGANIZATION_SLUG='avx', CACHES=LOCMEM)
class CallBackupTests(EnvMixin, TestCase):
    # 1:45 PM Eastern on 5 Nov 2026 (EST, UTC-5).
    NOW = datetime(2026, 11, 5, 18, 45, tzinfo=dt_timezone.utc)

    def setUp(self):
        super().setUp()
        from accounts.models import Organization, User
        from analytics.models import CallRecord
        from support import system_status as S
        p = mock.patch.object(S, '_cfg', return_value='')
        p.start()
        self.addCleanup(p.stop)
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        User.objects.create(username='o', email='o@x.io', organization=self.org, role='admin', is_superuser=True)
        r = CallRecord.objects.create(organization=self.org, twilio_call_sid='s1', caller_number='5551112222',
                                      called_number='+18334973067', campaign_name='C-07', buyer_name='Q08',
                                      publisher_name='PDSO', status='completed', duration_seconds=95)
        CallRecord.objects.filter(pk=r.pk).update(created_at=datetime(2026, 11, 5, 15, 0, tzinfo=dt_timezone.utc))
        self.env(TELEGRAM_NONPRIORITY_THREAD_ID='9')

    def run_backup(self, now=None):
        answer = mock.Mock(ok=True, content=sent(GROUP, 50).encode())
        with mock.patch('requests.post', return_value=answer) as post:
            out = N.send_call_backup(now or self.NOW)
        return out, post

    def test_todays_calls_as_excel_to_non_priority(self):
        out, post = self.run_backup()
        self.assertEqual(out, 'sent today: 1')
        (url,), kw = post.call_args
        self.assertTrue(url.endswith('/sendDocument'))
        self.assertEqual((kw['data']['chat_id'], kw['data']['message_thread_id']), (GROUP, '9'))
        self.assertIn('until 1:45 PM EST · 1 calls', kw['data']['caption'])
        name, content, mime = kw['files']['document']
        self.assertEqual(name, 'Avortyx Calls 2026-11-05 1345 EST.xlsx')
        book = zipfile.ZipFile(io.BytesIO(content))
        names = book.read('xl/workbook.xml').decode()
        for sheet in ('Call Log', 'Summary', 'By Campaign', 'By Publisher', 'By Buyer'):
            self.assertIn(sheet, names)
        self.assertIn('PDSO', ''.join(book.read(n).decode() for n in book.namelist() if n.startswith('xl/')))
        self.assertEqual([(e['id'], e['kind']) for e in A.entries()], [(50, 'nonpriority')])

    def test_after_midnight_the_whole_day_once(self):
        just_after = datetime(2026, 11, 6, 5, 10, tzinfo=dt_timezone.utc)        # 12:10 AM EST, 6 Nov
        out, post = self.run_backup(just_after)
        self.assertEqual(out, 'sent final 2026-11-05: 1')
        self.assertEqual(post.call_args.kwargs['files']['document'][0], 'Avortyx Calls 2026-11-05 FINAL.xlsx')
        out, post = self.run_backup(just_after)
        self.assertEqual(out, 'nothing to send')                                 # once only
        post.assert_not_called()

    def test_never_into_notifications_and_can_be_switched_off(self):
        self.env()                                                               # no topic set
        out, post = self.run_backup()
        self.assertIn('no Non Priority topic', out)
        post.assert_not_called()
        self.env(TELEGRAM_NONPRIORITY_THREAD_ID='9', NONPRIORITY_CALL_BACKUP='off')
        out, post = self.run_backup()
        self.assertIn('off', out)
        post.assert_not_called()
