"""CH-310: a buyer's report connects to Telegram through our own bot.

Run:  python manage.py test buyers.test_telegram_connect
"""
import json
from datetime import datetime, time
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from buyers.destination import Destination
from buyers.models import Buyer, BuyerTelegramReport

ET = ZoneInfo('America/New_York')


@override_settings(ALLOWED_HOSTS=['*'], TELEGRAM_BOT_TOKEN='t',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
@mock.patch('buyers.telegram_report.bot_username', return_value='@AVSPTBOT')
class TelegramConnectTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='a', email='a@x.io', organization=self.org,
                                         role='admin', is_active=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')
        self.buyer = Buyer.objects.create(organization=self.org, name='MTG')
        self.dest = Destination.objects.create(organization=self.org, buyer=self.buyer, name='MTG|3566',
                                               tfn='+18883413566', enabled=True)
        self.hook = Client()

    def link(self):
        r = self.api.post(f'/api/buyers/{self.buyer.id}/telegram-report/connect-link')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def telegram(self, text, chat_id, chat_type='private'):
        """An update from Telegram to our webhook; returns the bot's replies."""
        body = {'update_id': 1, 'message': {'message_id': 1, 'text': text,
                                            'chat': {'id': chat_id, 'type': chat_type},
                                            'from': {'id': 7, 'username': 'someone'}}}
        with mock.patch('requests.post') as post:
            r = self.hook.post('/api/support/telegram-webhook', json.dumps(body), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        return [c.kwargs['json']['text'] for c in post.call_args_list]

    def report(self):
        return BuyerTelegramReport.objects.get(buyer=self.buyer)

    def test_the_link_opens_our_bot_with_a_code(self, _):
        out = self.link()
        self.assertTrue(out['url'].startswith('https://t.me/AVSPTBOT?start=rpt_'))
        self.assertTrue(out['group_url'].startswith('https://t.me/AVSPTBOT?startgroup=rpt_'))
        self.assertEqual(out['bot'], '@AVSPTBOT')

    def test_opening_the_link_connects_the_chat(self, _):
        payload = self.link()['command'].split()[1]
        replies = self.telegram(f'/start {payload}', 555111)
        self.assertEqual(self.report().chat_id, '555111')
        self.assertIn('Connected', replies[0])
        self.assertIn('MTG', replies[0])
        # Connecting does not switch the report on or approve it.
        self.assertFalse(self.report().enabled)
        self.assertFalse(self.report().buyer_approved)

    def test_a_group_connects_with_the_bots_name_after_start(self, _):
        payload = self.link()['command'].split()[1]
        self.telegram(f'/start@AVSPTBOT {payload}', -1001234567890, 'supergroup')
        self.assertEqual(self.report().chat_id, '-1001234567890')

    def test_a_link_works_once(self, _):
        payload = self.link()['command'].split()[1]
        self.telegram(f'/start {payload}', 555111)
        replies = self.telegram(f'/start {payload}', 999999)
        self.assertEqual(self.report().chat_id, '555111')
        self.assertIn('expired', replies[0])

    def test_an_unknown_code_changes_nothing(self, _):
        BuyerTelegramReport.objects.create(buyer=self.buyer, chat_id='42')
        replies = self.telegram('/start rpt_nonsense', 999999)
        self.assertEqual(self.report().chat_id, '42')
        self.assertIn('expired', replies[0])

    def test_the_buyer_can_connect_its_own_chat(self, _):
        u = User.objects.create(username='bm', email='m@buyer.io', organization=self.org, role='buyer',
                                buyer=self.buyer, is_active=True)
        me = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')
        out = me.post('/api/buyers/my/telegram-report/connect-link').json()
        self.telegram(f'/start {out["command"].split()[1]}', 777)
        self.assertEqual(self.report().chat_id, '777')
        self.assertTrue(me.get('/api/buyers/my/telegram-report').json()['chat_connected'])
        # A buyer login cannot make a link for another buyer, nor for itself the admin way.
        self.assertEqual(me.post(f'/api/buyers/{self.buyer.id}/telegram-report/connect-link').status_code, 403)

    def test_id_command_gives_the_chat_id(self, _):
        self.assertIn('-100555', self.telegram('/id', -100555, 'supergroup')[0])
        self.assertIn('321', self.telegram('/id@AVSPTBOT', 321)[0])

    def test_added_to_a_group_says_hello_with_the_chat_id(self, _):
        body = {'update_id': 2, 'my_chat_member': {
            'chat': {'id': -1009, 'type': 'supergroup'},
            'old_chat_member': {'status': 'left'}, 'new_chat_member': {'status': 'member'}}}
        with mock.patch('requests.post') as post:
            self.hook.post('/api/support/telegram-webhook', json.dumps(body), content_type='application/json')
        self.assertIn('-1009', post.call_args.kwargs['json']['text'])

    def test_profile_linking_still_works(self, _):
        # '/start <code>' without rpt_ is still the profile link (unchanged).
        from datetime import timedelta
        from django.utils import timezone
        from accounts.models import TelegramLinkCode
        TelegramLinkCode.objects.create(user=self.admin, code='abc123',
                                        expires_at=timezone.now() + timedelta(minutes=10))
        self.telegram('/start abc123', 4242)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.telegram_chat_id, '4242')

    def test_test_send_goes_to_the_admins_own_telegram_only(self, _):
        BuyerTelegramReport.objects.create(buyer=self.buyer, chat_id='555111', enabled=True,
                                           send_time=time(21, 0), buyer_approved=False)
        CallRecord.objects.create(
            id=__import__('uuid').uuid4(), organization=self.org, buyer_id=self.buyer.id,
            caller_number='+15550001111', called_number='+18443754175', destination_number='18883413566',
            status='completed', duration_seconds=70, started_at=datetime.now(ET).replace(hour=0, minute=5))
        url = f'/api/buyers/{self.buyer.id}/telegram-report/send-test'
        self.assertIn('Link your own Telegram', self.api.post(url).json()['detail'])
        User.objects.filter(pk=self.admin.pk).update(telegram_chat_id='9001')
        with mock.patch('requests.post', return_value=mock.Mock(status_code=200)) as post:
            r = self.api.post(url)
        self.assertEqual(r.status_code, 200, r.content)
        chats = {c.kwargs['data']['chat_id'] for c in post.call_args_list}
        self.assertEqual(chats, {'9001'})   # never the buyer's chat
        self.assertTrue(post.call_args_list[0].kwargs['data']['text'].startswith('TEST'))
        # The buyer had not approved: a real send is still refused.
        self.assertEqual(self.api.post(f'/api/buyers/{self.buyer.id}/telegram-report/send').status_code, 400)
