"""CH-669: support chat visitors who only say "hi" (12 Nov).

Run:  python manage.py test support.test_greetings
"""
import json
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from support import greetings
from support.models import SupportMessage, SupportSession

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/130.0.0.0 Safari/537.36')


class GreetingWordsTests(SimpleTestCase):
    def test_greetings_only(self):
        for t in ('Hi', 'hiii', 'HELLO!!', 'hey there', 'Good morning sir', 'hi team', '👋', '?', 'hlo',
                  'Hi is anyone there?'):
            self.assertTrue(greetings.is_greeting(t), t)

    def test_anything_said_is_a_real_message(self):
        for t in ('Avortyxsupport Test HI', 'hi i need help', 'Hello, my calls are not coming', 'Hi how are you',
                  '📷 Picture', 'ok'):
            self.assertFalse(greetings.is_greeting(t), t)

    def test_device(self):
        self.assertEqual(greetings.device(UA), 'Chrome 130 on Windows 10/11')
        self.assertEqual(greetings.device(
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) '
            'Version/17.5 Mobile/15E148 Safari/604.1'), 'Safari 17 on iPhone')
        self.assertEqual(greetings.device(''), 'unknown')

    def test_a_private_address_is_not_looked_up(self):
        with mock.patch('requests.get') as get:
            self.assertEqual(greetings.location('10.0.0.5'), 'private network (10.0.0.5)')
        get.assert_not_called()


@override_settings(CACHES=CACHE, ALLOWED_HOSTS=['*'], SUPPORT_CHAT_RATE='1000/m', SUPPORT_MESSAGE_RATE='1000/m',
                   SUPPORT_GREETING_LIMIT=3, SUPPORT_GREETING_BLOCK_HOURS=24)
class GreetingBlockTests(TestCase):
    def setUp(self):
        cache.clear()
        p = [mock.patch('support.api.send_telegram_support'), mock.patch('support.nonpriority.post'),
             mock.patch('support.greetings.location', return_value='Nairobi, Nairobi, KE (IP 41.90.1.2)')]
        self.team, self.nonpriority, _ = [x.start() for x in p]
        for x in p:
            self.addCleanup(x.stop)

    def client_for(self, ip='41.90.1.2'):
        return Client(REMOTE_ADDR=ip, HTTP_USER_AGENT=UA)

    def start(self, text, ip='41.90.1.2', browser=''):
        r = self.client_for(ip).post('/api/support/chat', json.dumps(
            {'name': 'V', 'email': 'v@x.io', 'message': text, 'browser_id': browser}), content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def follow(self, session_id, text, ip='41.90.1.2', browser=''):
        r = self.client_for(ip).post(f'/api/support/chat/{session_id}', json.dumps(
            {'message': text, 'browser_id': browser}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_three_hi_go_the_fourth_is_held_for_24_hours_and_the_team_is_told(self):
        for _ in range(3):                                  # three new chats, as in the screenshot
            self.assertNotIn('blocked', self.start('Hi'))
        self.assertEqual(self.team.call_count, 3)
        d = self.start('Hi')
        self.assertEqual((d['blocked'], d['message']), (True, greetings.NOTICE))
        self.assertEqual(self.team.call_count, 3)          # not sent to the team
        s = SupportSession.objects.get(id=d['session_id'])
        self.assertEqual(s.greeting_block, 'day')
        self.assertAlmostEqual((s.greeting_block_until - timezone.now()).total_seconds(), 24 * 3600, delta=60)
        m = SupportMessage.objects.get(session=s)
        self.assertEqual((m.message, m.greeting, m.held), ('Hi', True, True))   # kept, never deleted
        text = self.nonpriority.call_args[0][0]
        self.assertIn('greetings blocked for 24 hours', text)
        self.assertIn('Location: Nairobi, Nairobi, KE (IP 41.90.1.2)', text)
        self.assertIn('Device: Chrome 130 on Windows 10/11', text)
        self.assertIn('V (v@x.io)', text)

    def test_a_real_question_still_reaches_the_team_while_blocked(self):
        for _ in range(4):
            sid = self.start('hello')['session_id']
        sent = self.team.call_count
        self.assertEqual(self.follow(sid, 'My calls are not coming in'), {'message': 'Message sent'})
        self.assertEqual(self.team.call_count, sent + 1)

    def test_greeting_again_within_the_24_hours_blocks_for_good(self):
        for _ in range(4):
            sid = self.start('hi')['session_id']
        self.assertEqual(self.nonpriority.call_count, 1)
        self.assertTrue(self.follow(sid, 'hii')['blocked'])
        self.assertIn('greetings blocked for good', self.nonpriority.call_args[0][0])
        # Long after the 24 hours: still held, and the team is not told again.
        SupportSession.objects.update(greeting_block_until=timezone.now() - timedelta(days=3))
        self.assertTrue(self.start('Hi')['blocked'])
        self.assertEqual(self.nonpriority.call_count, 2)
        self.assertEqual(self.follow(sid, 'I need 20 numbers please'), {'message': 'Message sent'})

    def test_after_the_24_hours_greetings_go_again_and_a_second_time_is_for_good(self):
        for _ in range(4):
            self.start('Hi')
        SupportSession.objects.update(greeting_block_until=timezone.now() - timedelta(minutes=1))
        SupportMessage.objects.update(created_at=timezone.now() - timedelta(hours=25))
        sent = self.team.call_count
        for _ in range(3):
            self.assertNotIn('blocked', self.start('Hi'))
        self.assertEqual(self.team.call_count, sent + 3)
        self.assertTrue(self.start('Hi')['blocked'])
        self.assertIn('for good', self.nonpriority.call_args[0][0])

    def test_the_same_browser_on_another_internet_address_is_the_same_person(self):
        for ip in ('41.90.1.2', '41.90.1.3', '102.0.0.9'):
            self.start('Hi', ip=ip, browser='b-123')
        self.assertTrue(self.start('hey', ip='197.1.1.1', browser='b-123')['blocked'])

    def test_someone_else_is_not_affected(self):
        for _ in range(4):
            self.start('Hi')
        self.assertNotIn('blocked', self.start('Hi', ip='105.1.1.1', browser='other'))

    def test_a_real_message_starts_the_count_again(self):
        sid = self.start('Hi')['session_id']
        self.follow(sid, 'Hi')
        self.follow(sid, 'How do I add a buyer?')
        for _ in range(3):
            self.assertNotIn('blocked', self.follow(sid, 'hello'))
        self.assertTrue(self.follow(sid, 'hello')['blocked'])
