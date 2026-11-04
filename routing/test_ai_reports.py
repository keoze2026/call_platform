"""CH-546: AI audit Parts 2 and 3 - the AI label in Reports for our team only,
the AI Label filter, repeat callers, evidence per publisher, the publisher
No Voice rate alert and the daily summary on Telegram.

Run:  python manage.py test routing.test_ai_reports
"""
import json
from datetime import timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import TeamReportChat
from buyers.models import Buyer
from campaigns.models import Campaign
from publishers.models import Publisher
from routing import ai_reports
from routing.models import CallLog
from routing.signals import mirror_call_log

ET = ZoneInfo('America/New_York')


@override_settings(ALLOWED_HOSTS=['*'], VOICE_TELEGRAM_ALERTS=True, REPORTS_BOT_TOKEN='t',
                   AI_RATE_ALERT_PERCENT=15, AI_RATE_ALERT_MIN_CALLS=10, AI_DAILY_SUMMARY_HOUR=9,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AiReportsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-07', status='active')
        self.buyer = Buyer.objects.create(organization=self.org, name='HOZ')
        self.pdso = Publisher.objects.create(organization=self.org, name='PDSO 1')
        self.ads = Publisher.objects.create(organization=self.org, name='ADS-3')
        TeamReportChat.objects.create(organization=self.org, chat_id='-100', title='Team')
        self.n = 0

    def call(self, label, publisher=None, caller=None, minutes_ago=5, **kw):
        self.n += 1
        c = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyer,
                                   publisher=publisher or self.pdso, caller_number=caller or f'+1555{self.n:07d}',
                                   called_number='+18334973067', twilio_call_sid=f's{self.n}', status='completed',
                                   duration=30, destination_number='+18334973067', ai_label=label,
                                   ai_checked_at=timezone.now() if label else None,
                                   ended_at=timezone.now() - timedelta(minutes=minutes_ago), **kw)
        CallLog.objects.filter(pk=c.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_ago),
            recording_url=f'https://rec.example.com/recordings/{c.id}.mp3')
        mirror_call_log(c.id)
        return CallLog.objects.get(pk=c.pk)

    def api(self, role, **kw):
        self.n += 1
        u = User.objects.create(username=f'{role}{self.n}', email=f'{role}{self.n}@x.io', organization=self.org,
                                role=role, **kw)
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')

    def today(self):
        return timezone.now().astimezone(ET).date().isoformat()

    # ── Part 2: Reports / Call Log ───────────────────────────────────────────

    def test_call_log_carries_the_label_for_our_team_only(self):
        self.call('agent_only')
        day = self.today()
        url = f'/api/analytics/calls?date_from={day}&date_to={day}&timezone=America/New_York'
        staff = self.api('admin').get(url).json()['items']
        self.assertEqual(staff[0]['ai_label'], 'agent_only')
        buyer = self.api('buyer', buyer=self.buyer).get(url).json()['items']
        self.assertIsNone(buyer[0]['ai_label'])

    def test_ai_label_filter(self):
        self.call('agent_only'), self.call('dead_caller'), self.call('valid'), self.call('')
        day = self.today()
        admin = self.api('admin')

        def ids(f):
            r = admin.get(f'/api/analytics/calls?date_from={day}&date_to={day}&timezone=America/New_York&ai_label={f}')
            return sorted(i['ai_label'] for i in r.json()['items'])
        self.assertEqual(ids('no_voice'), ['agent_only', 'dead_caller'])
        self.assertEqual(ids('valid'), ['valid'])
        self.assertEqual(ids('pending'), [''])
        # A buyer's filter is ignored: it neither narrows nor reveals anything.
        r = self.api('buyer', buyer=self.buyer).get(
            f'/api/analytics/calls?date_from={day}&date_to={day}&timezone=America/New_York&ai_label=valid')
        self.assertEqual(len(r.json()['items']), 4)

    def test_repeat_callers_and_evidence(self):
        for _ in range(3):
            self.call('dead_caller', caller='+15550001111')
        self.call('valid', caller='+15550001111')
        self.call('agent_beep', publisher=self.ads, caller='+15550002222')
        admin = self.api('admin')
        rep = admin.get('/api/ai-audit/repeat-callers').json()['items']
        self.assertEqual([(r['caller'], r['no_voice'], r['calls']) for r in rep], [('+15550001111', 3, 4)])
        self.assertEqual(rep[0]['labels'], {'No Voice | Dead Caller': 3})
        ev = admin.get(f'/api/ai-audit/evidence?publisher_id={self.ads.id}').json()
        self.assertEqual(ev['count'], 1)
        self.assertEqual((ev['items'][0]['label'], ev['items'][0]['publisher']),
                         ('No Voice | Only Agent + Beeping', 'ADS-3'))
        self.assertTrue(ev['items'][0]['recording_url'].endswith('.mp3'))
        for client in (self.api('buyer', buyer=self.buyer), self.api('publisher')):
            self.assertEqual(client.get('/api/ai-audit/repeat-callers').status_code, 403)
            self.assertEqual(client.get('/api/ai-audit/evidence').status_code, 403)

    # ── Part 3: Telegram ─────────────────────────────────────────────────────

    def test_publisher_over_15_percent_in_the_hour_alerted_once(self):
        for i in range(10):
            self.call('dead_caller' if i < 2 else 'valid')              # PDSO 1: 2 of 10 = 20 %
        for i in range(10):
            self.call('agent_only' if i < 1 else 'valid', publisher=self.ads)   # ADS-3: 10 %
        for _ in range(3):
            self.call('dead_caller', publisher=self.ads, minutes_ago=90)   # outside the hour
        with mock.patch('analytics.team_report.telegram') as tg:
            self.assertEqual(ai_reports.rate_alerts(), 1)
            text = tg.call_args[0][1]['text']
            self.assertIn('<b>PDSO 1</b>: 2 of 10 checked calls (20%) - Dead Caller 2', text)
            self.assertNotIn('ADS-3', text)
            self.assertEqual(ai_reports.rate_alerts(), 0)                # once per hour

    def test_too_few_calls_are_not_a_rate(self):
        for _ in range(3):
            self.call('dead_caller')
        with mock.patch('analytics.team_report.telegram') as tg:
            self.assertEqual(ai_reports.rate_alerts(), 0)
        tg.assert_not_called()

    def test_daily_summary_per_label_and_publisher_once(self):
        # An hour before today's EST midnight: yesterday.
        now = timezone.now().astimezone(ET)
        minutes = int((now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() // 60) + 60
        self.call('dead_caller', minutes_ago=minutes)
        self.call('agent_beep', minutes_ago=minutes)
        self.call('valid', publisher=self.ads, minutes_ago=minutes)
        self.call('valid', minutes_ago=5)                                   # today: not in it
        ten_am = now.replace(hour=10, minute=0)
        with mock.patch('analytics.team_report.telegram') as tg:
            self.assertEqual(ai_reports.daily_summary(now=ten_am), 1)
            text = tg.call_args[0][1]['text']
            self.assertIn('3 calls checked · No Voice 2 (66.7%)', text)
            self.assertIn('No Voice | Dead Caller: 1', text)
            self.assertIn('PDSO 1: 2 / 2 (100%)', text)
            self.assertIn('ADS-3: 0 / 1 (0%)', text)
            self.assertEqual(ai_reports.daily_summary(now=ten_am), 0)       # once a day
        with mock.patch('analytics.team_report.telegram') as tg:
            self.assertEqual(ai_reports.daily_summary(now=now.replace(hour=8, minute=0)), 0)  # before 9 AM

    @override_settings(VOICE_TELEGRAM_ALERTS=False)
    def test_switch_off_sends_nothing(self):
        for _ in range(12):
            self.call('dead_caller')
        with mock.patch('analytics.team_report.telegram') as tg:
            self.assertEqual(ai_reports.rate_alerts(), 0)
        tg.assert_not_called()


class ReportsSeeTheLabelArriveTests(AiReportsTests):
    """CH-546: the Reports fingerprint moves when a call gets its label."""

    def test_label_moves_the_report_stamp(self):
        import io
        from analytics.report_stamp import report_stamp
        from django.core.management import call_command
        from analytics.schemas import AnalyticsFilterSchema
        c = self.call('')
        user = User.objects.create(username='s1', email='s1@x.io', organization=self.org, role='admin')
        day = self.today()
        f = AnalyticsFilterSchema(date_from=day, date_to=day, timezone='America/New_York')
        before = report_stamp(user, f)['stamp']
        with mock.patch('sys.stdin', io.StringIO(json.dumps({'id': str(c.id), 'channels': 2, 'speech': 4,
                                                               'audio': 30, 'caller_speech': 0,
                                                               'agent_speech': 4, 'beep': 0}) + '\n')):
            with mock.patch('analytics.team_report.telegram'):
                call_command('voice_results', stdout=io.StringIO())
        self.assertNotEqual(report_stamp(user, f)['stamp'], before)
