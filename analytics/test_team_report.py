"""CH-347: the team report from our own reports bot.

Run:  python manage.py test analytics.test_team_report
"""
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics import team_report as T
from analytics.models import CallRecord, TeamReportChat

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
BOT = dict(REPORTS_BOT_TOKEN='123:abc', REPORTS_BOT_USERNAME='AvortyxReportsBot', CACHES=CACHE,
           ALLOWED_HOSTS=['*'])
# 10:00 AM Eastern on 29 Oct 2026 (EDT, UTC-4).
NOW = datetime(2026, 10, 29, 14, 0, tzinfo=dt_timezone.utc)


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def __call__(self, method, data, files=None):
        self.sent.append((method, data, files))
        r = mock.Mock(status_code=200)
        r.json.return_value = {'ok': True, 'result': {'username': 'AvortyxReportsBot'}}
        return r


@override_settings(**BOT)
class TeamReportTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin')
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')
        self.n = 0

    def call(self, when, campaign='C-07', publisher='Crug', buyer='PDSO', status='completed', revenue='5',
             payout='3', converted=True, sid=None):
        self.n += 1
        r = CallRecord.objects.create(
            organization=self.org, twilio_call_sid=sid or f'c{self.n}', caller_number='+15551112222',
            campaign_name=campaign, publisher_name=publisher, buyer_name=buyer, status=status,
            revenue=Decimal(revenue), payout=Decimal(payout), profit=Decimal(revenue) - Decimal(payout),
            is_converted=converted, duration_seconds=90 if status == 'completed' else 0)
        CallRecord.objects.filter(pk=r.pk).update(created_at=when)

    def connect(self, chat_id='777', title='Sir'):
        with mock.patch.object(T, 'telegram', FakeTelegram()):
            link = self.api.post('/api/team-report/connect-link').json()
        code = link['url'].split('start=')[1]
        fake = FakeTelegram()
        with mock.patch.object(T, 'telegram', fake):
            r = Client().post('/api/team-report-bot/webhook', content_type='application/json',
                              data=json.dumps({'message': {'text': f'/start {code}',
                                                           'chat': {'id': int(chat_id), 'type': 'private',
                                                                    'first_name': title}}}),
                              HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=T.webhook_secret())
        self.assertEqual(r.status_code, 200)
        return link, fake

    def test_connecting_a_chat_needs_no_chat_id(self):
        link, fake = self.connect()
        self.assertEqual(link['url'].split('?')[0], 'https://t.me/AvortyxReportsBot')
        self.assertIn('startgroup=team_', link['group_url'])
        c = TeamReportChat.objects.get()
        self.assertEqual((c.chat_id, c.title, c.organization, c.connected_by), ('777', 'Sir', self.org, self.admin))
        self.assertIn('Connected', fake.sent[0][1]['text'])
        body = self.api.get('/api/team-report/').json()
        self.assertEqual(body['bot'], '@AvortyxReportsBot')
        self.assertEqual(body['chats'][0]['title'], 'Sir')

    def test_a_link_works_once(self):
        link, _ = self.connect()
        code = link['url'].split('start=')[1]
        fake = FakeTelegram()
        with mock.patch.object(T, 'telegram', fake):
            Client().post('/api/team-report-bot/webhook', content_type='application/json',
                          data=json.dumps({'message': {'text': f'/start {code}', 'chat': {'id': 999}}}),
                          HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=T.webhook_secret())
        self.assertEqual(TeamReportChat.objects.count(), 1)
        self.assertIn('expired', fake.sent[0][1]['text'])

    def test_the_webhook_needs_the_secret(self):
        r = Client().post('/api/team-report-bot/webhook', content_type='application/json', data='{}')
        self.assertEqual(r.status_code, 403)

    def test_stop_and_removal_switch_it_off(self):
        self.connect()
        with mock.patch.object(T, 'telegram', FakeTelegram()):
            T.handle_update({'message': {'text': '/stop@AvortyxReportsBot', 'chat': {'id': 777}}})
        self.assertFalse(TeamReportChat.objects.get().enabled)

    def test_the_daily_report(self):
        self.connect()
        yesterday = datetime(2026, 10, 28, 16, 0, tzinfo=dt_timezone.utc)
        self.call(yesterday)
        self.call(yesterday, campaign='C-02', publisher='BB1', buyer='Q08', status='no_answer', revenue='0',
                  payout='0', converted=False)
        self.call(yesterday, sid='tfntest-1', status='no_answer', revenue='0', payout='0', converted=False)
        self.call(NOW - timedelta(minutes=5))                       # today: not in yesterday's report
        fake = FakeTelegram()
        with mock.patch.object(T, 'telegram', fake):
            self.assertEqual(T.send_due(NOW), {'sent': 1, 'failed': 0})
            self.assertEqual(T.send_due(NOW), {'sent': 0, 'failed': 0})   # once a day
        text = fake.sent[0][1]['text']
        self.assertIn('Wed 28 Oct 2026', text)
        self.assertIn('<b>2</b> calls', text)
        self.assertIn('<b>1</b> connected (50%)', text)
        self.assertIn('Profit: <b>$2.00</b>', text)
        method, data, files = fake.sent[1]
        self.assertEqual(method, 'sendDocument')
        z = zipfile.ZipFile(io.BytesIO(files['document'][1]))
        wb = z.read('xl/workbook.xml').decode()
        for name in ('Summary', 'By Campaign', 'By Publisher', 'By Buyer'):
            self.assertIn(f'name="{name}"', wb)
        self.assertIn('C-02', z.read('xl/worksheets/sheet2.xml').decode())

    def test_not_before_its_time(self):
        self.connect()
        early = datetime(2026, 10, 29, 12, 0, tzinfo=dt_timezone.utc)   # 8 AM Eastern
        with mock.patch.object(T, 'telegram', FakeTelegram()) as _:
            self.assertEqual(T.send_due(early), {'sent': 0, 'failed': 0})

    def test_a_failed_send_is_tried_three_times(self):
        self.connect()
        with mock.patch.object(T, 'telegram', side_effect=T.TeamReportError('blocked')):
            for _ in range(5):
                T.send_due(NOW)
        c = TeamReportChat.objects.get()
        self.assertEqual(c.attempts, 3)
        self.assertIn('blocked', c.last_result)

    def test_partners_cannot_use_it(self):
        from publishers.models import Publisher
        pub = Publisher.objects.create(organization=self.org, name='Crug')
        p = User.objects.create(username='p', email='p@x.io', organization=self.org, role='publisher', publisher=pub)
        api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(p)}')
        self.assertEqual(api.get('/api/team-report/').status_code, 403)
        self.assertEqual(api.post('/api/team-report/connect-link').status_code, 403)

    def test_send_time_and_switch(self):
        self.connect()
        c = TeamReportChat.objects.get()
        r = self.api.patch(f'/api/team-report/chats/{c.id}', content_type='application/json',
                           data=json.dumps({'send_time': '07:30', 'enabled': False}))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['send_time'], r.json()['enabled']), ('07:30', False))
        bad = self.api.patch(f'/api/team-report/chats/{c.id}', content_type='application/json',
                             data=json.dumps({'send_time': 'soon'}))
        self.assertEqual(bad.status_code, 400)

    @override_settings(REPORTS_BOT_TOKEN='')
    def test_without_a_bot_nothing_is_sent(self):
        self.assertEqual(self.api.post('/api/team-report/connect-link').status_code, 400)
        self.assertEqual(T.send_due(NOW), {'sent': 0, 'failed': 0})
