"""CH-633 (customer accounts point 7): messages the platform sends to OUR team
stay ours, and a customer account gets nothing of ours.

- The Telegram bot's /status (our services) and /balance (sets our carrier
  balance) answer our account's admins only, never a customer account's admin
  who linked their Telegram.
- The AI audit's Telegram messages (No Voice alerts, rate alerts, daily
  summary) go to our team chat only - the AI audit is ours (CH-619).
- The morning status report counts our account only.

Run:  python manage.py test accounts.test_point7_team_messages
"""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization, User
from analytics.models import TeamReportChat
from campaigns.models import Campaign
from routing import ai_labels, ai_reports, voice_alerts
from routing.models import CallLog
from support import system_status as S

LABEL = sorted(ai_labels.NO_VOICE)[0]


@override_settings(MAIN_ORGANIZATION_SLUG='interns', VOICE_TELEGRAM_ALERTS=True, REPORTS_BOT_TOKEN='t',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class Point7Base(TestCase):
    def setUp(self):
        cache.clear()
        self.ours = Organization.objects.create(name='Interns', slug='interns')
        self.theirs = Organization.objects.create(name='Texora', slug='texora')
        self.n = 0

    def admin(self, org, chat):
        self.n += 1
        return User.objects.create(username=f'a{self.n}', email=f'a{self.n}@x.io', organization=org,
                                   role='admin', is_active=True, telegram_chat_id=chat)

    def no_voice_call(self, org):
        self.n += 1
        campaign = Campaign.objects.create(organization=org, name=f'C{self.n}')
        return CallLog.objects.create(organization=org, campaign=campaign, caller_number=f'+1555000{self.n:04d}',
                                      called_number='+18005550001', twilio_call_sid=f's{self.n}',
                                      status='completed', duration=30, ai_label=LABEL,
                                      ai_checked_at=timezone.now())


class BotCommandTests(Point7Base):
    def test_our_admin_may_ask(self):
        self.admin(self.ours, '555')
        self.assertTrue(S.allowed_chat('555'))

    def test_a_customer_accounts_admin_may_not(self):
        self.admin(self.theirs, '666')
        self.assertFalse(S.allowed_chat('666'))

    @override_settings(TELEGRAM_CHAT_ID='-100777')
    def test_our_configured_chats_still_may(self):
        self.assertTrue(S.allowed_chat('-100777'))

    def test_balance_from_a_customer_admin_changes_nothing(self):
        self.admin(self.theirs, '666')
        with mock.patch.object(S, 'set_balance') as set_balance, \
                mock.patch('support.telegram_webhook._reply'):
            from support.telegram_webhook import _handle_balance
            _handle_balance('666', '/balance 1.00')
        set_balance.assert_not_called()


class AiTelegramTests(Point7Base):
    def setUp(self):
        super().setUp()
        TeamReportChat.objects.create(organization=self.ours, chat_id='-100', title='Ours')
        TeamReportChat.objects.create(organization=self.theirs, chat_id='-200', title='Theirs')

    def chats_sent_to(self, send):
        with mock.patch('analytics.team_report.configured', return_value=True), \
                mock.patch('analytics.team_report.telegram') as tg:
            send()
        return [c.args[1]['chat_id'] for c in tg.call_args_list]

    def test_no_voice_alerts_reach_our_chat_only(self):
        ids = [self.no_voice_call(self.ours).id, self.no_voice_call(self.theirs).id]
        self.assertEqual(self.chats_sent_to(lambda: voice_alerts.send_no_voice(ids)), ['-100'])

    def test_ai_report_messages_reach_our_chat_only(self):
        self.assertEqual(self.chats_sent_to(lambda: ai_reports._send(self.theirs.id, 'x')), [])
        self.assertEqual(self.chats_sent_to(lambda: ai_reports._send(self.ours.id, 'x')), ['-100'])


@override_settings(DAILY_STATUS_ORGS=[])
class MorningReportTests(Point7Base):
    def test_only_our_account_is_counted(self):
        from accounts.management.commands.daily_status import Command
        self.no_voice_call(self.ours)
        self.no_voice_call(self.theirs)
        orgs = Command()._reporting_orgs(timezone.now().date() - timedelta(days=1))
        self.assertEqual([o.id for o in orgs], [self.ours.id])
