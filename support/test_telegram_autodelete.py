"""CH-332: the bot's notifications are deleted from Telegram after a week.

Run:  python manage.py test support.test_telegram_autodelete
"""
import importlib.util
import json
import os
import tempfile
import time
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from support import system_status as S
from support import telegram_autodelete as A

DAY = 86400
GROUP = '-1001234567890'


def sent(chat, mid):
    return json.dumps({'ok': True, 'result': {'message_id': mid, 'chat': {'id': int(chat)}}})


class AutoDeleteTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        patches = [mock.patch.object(A, 'LOG', os.path.join(self.tmp, 'sent.jsonl')),
                   mock.patch.object(A, 'ENV_FILE', os.path.join(self.tmp, '.env')),
                   mock.patch.dict(os.environ, {'TELEGRAM_AUTODELETE_DAYS': '', 'TELEGRAM_AUTODELETE_KEEP': ''})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def note(self, mid, age_days, kind='status'):
        with mock.patch('time.time', return_value=time.time() - age_days * DAY):
            self.assertTrue(A.remember(GROUP, sent(GROUP, mid), kind))

    def test_group_messages_are_noted_private_chats_are_not(self):
        self.assertTrue(A.remember(GROUP, sent(GROUP, 5)))
        self.assertFalse(A.remember('123456', sent('123456', 6)))      # private: 48 h limit
        self.assertFalse(A.remember(GROUP, '{"ok": false}'))
        self.assertFalse(A.remember(GROUP, 'not json'))
        self.assertEqual([(e['chat'], e['id']) for e in A.entries()], [(GROUP, 5)])

    def test_days_and_kept_kinds_come_from_env(self):
        with open(A.ENV_FILE, 'w') as f:
            f.write('TELEGRAM_AUTODELETE_KEEP=eod\n')
        self.assertFalse(A.remember(GROUP, sent(GROUP, 1), 'eod'))
        self.assertTrue(A.remember(GROUP, sent(GROUP, 2), 'server'))
        with mock.patch.dict(os.environ, {'TELEGRAM_AUTODELETE_DAYS': '0'}):
            self.assertFalse(A.remember(GROUP, sent(GROUP, 3), 'server'))
            self.assertEqual(A.prune('t'), {'off': True})

    def test_week_old_messages_are_deleted_younger_ones_stay(self):
        self.note(1, 8)
        self.note(2, 6.9)
        calls = []
        done = A.prune('t', delete=lambda tok, chat, mid: calls.append(mid) or 'ok')
        self.assertEqual(calls, [1])
        self.assertEqual((done['deleted'], done['waiting']), (1, 1))
        self.assertEqual([e['id'] for e in A.entries()], [2])

    def test_refused_is_retried_daily_then_given_up(self):
        self.note(1, 8)
        self.note(2, 31)
        done = A.prune('t', delete=lambda *a: 'refused')
        self.assertEqual(done['refused'], 2)
        left = A.entries()
        self.assertEqual([e['id'] for e in left], [1])              # 31 days old: given up
        self.assertGreater(left[0]['retry_at'], time.time() + DAY - 60)
        tried = []
        A.prune('t', delete=lambda tok, chat, mid: tried.append(mid) or 'ok')
        self.assertEqual(tried, [])                                  # not again within the day
        A.prune('t', now=time.time() + DAY + 1, delete=lambda tok, chat, mid: tried.append(mid) or 'ok')
        self.assertEqual((tried, A.entries()), ([1], []))

    def test_gone_is_dropped_errors_are_kept(self):
        self.note(1, 8)
        self.note(2, 8)
        A.prune('t', delete=lambda tok, chat, mid: 'gone' if mid == 1 else 'error')
        self.assertEqual([e['id'] for e in A.entries()], [2])

    def test_messages_sent_while_deleting_are_kept(self):
        self.note(1, 8)

        def delete(tok, chat, mid):
            A.remember(GROUP, sent(GROUP, 99))      # an alert goes out meanwhile
            return 'ok'
        done = A.prune('t', delete=delete)
        self.assertEqual([e['id'] for e in A.entries()], [99])
        self.assertEqual(done['waiting'], 1)

    @override_settings(TELEGRAM_BOT_TOKEN='t', TELEGRAM_SUPPORT_CHAT_ID=GROUP, TELEGRAM_NOTIFY_THREAD_ID='7')
    def test_status_messages_are_noted(self):
        answer = mock.Mock(ok=True, content=sent(GROUP, 42).encode())
        with mock.patch('requests.post', return_value=answer), mock.patch.object(S, '_cfg', return_value=''):
            self.assertTrue(S.post('⚠️ test'))
        self.assertEqual([(e['id'], e['kind']) for e in A.entries()], [(42, 'status')])

    def test_host_alerts_are_noted(self):
        path = os.path.join(str(settings.BASE_DIR), 'scripts', 'host_watch.py')
        spec = importlib.util.spec_from_file_location('host_watch_ad', path)
        hw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hw)
        hw._env = lambda name: {'TELEGRAM_BOT_TOKEN': 't', 'STATUS_CHAT_ID': GROUP}.get(name, '')
        resp = mock.MagicMock(status=200)
        resp.read.return_value = sent(GROUP, 77).encode()
        resp.__enter__.return_value = resp
        with mock.patch('urllib.request.urlopen', return_value=resp):
            self.assertTrue(hw.telegram('🔴 test'))
        self.assertEqual([(e['id'], e['kind']) for e in A.entries()], [(77, 'server')])

    @override_settings(TELEGRAM_BOT_TOKEN='t', TELEGRAM_SUPPORT_CHAT_ID=GROUP)
    def test_status_line_says_when_the_bot_is_not_admin(self):
        with mock.patch.object(S, '_cfg', return_value=''), mock.patch.object(cache, 'get', return_value=None):
            with mock.patch.object(A, 'bot_can_delete', return_value='no'):
                it = S.check_autodelete()[0]
                self.assertEqual(it.status, S.WARN)
                self.assertIn('not an admin', it.detail)
            with mock.patch.object(A, 'bot_can_delete', return_value='yes'):
                it = S.check_autodelete()[0]
                self.assertEqual(it.status, S.OK)
                self.assertIn('after 7 days', it.detail)
        with override_settings(TELEGRAM_SUPPORT_CHAT_ID='12345'), mock.patch.object(S, '_cfg', return_value=''):
            self.assertEqual(S.check_autodelete()[0].status, S.NA)
