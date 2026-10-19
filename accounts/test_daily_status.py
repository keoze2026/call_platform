"""The daily report's layout (Telegram HTML) and its fall-back to plain text."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.management.commands.daily_status import Command
from accounts.models import Organization
from campaigns.models import Campaign
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=CACHE)
class DailyStatusLayoutTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C')
        self.day = timezone.now().date()
        self.n = 0

    def call(self, status, block_reason=''):
        self.n += 1
        CallLog.objects.create(
            organization=self.org, campaign=self.campaign,
            caller_number='+1555000%04d' % self.n, called_number='+18005550001',
            twilio_call_sid='sid-%s' % self.n, status=status, block_reason=block_reason)

    def report(self, checks=None, failed=None):
        return Command()._compose(self.day, None, checks, failed)

    def test_sections_and_bold_figures(self):
        for _ in range(3):
            self.call('completed')
        self.call('missed', 'Campaign cap reached')
        text = self.report()
        self.assertIn('<b>▍ AVORTYX</b>', text)
        self.assertIn(f'<b>Daily Report · {self.day:%A, %-d %B}</b>', text)
        self.assertIn('<b>▍ Call performance</b>', text)
        self.assertIn('<b>4</b> calls received', text)
        self.assertIn('<b>3</b> connected · <b>75%</b> connection rate', text)
        self.assertIn('<b>1</b> turned away · cap reached', text)
        self.assertIn('Availability: Not measured', text)
        self.assertIn('<b>▍ Financials</b>', text)
        self.assertIn('<b>▍ Live status</b>', text)
        self.assertIn('<b>Balance:</b> $', text)
        self.assertIn('<b>Buyers accepting calls:</b> 0', text)

    def test_money_is_in_a_code_block(self):
        self.call('completed')
        text = self.report()
        self.assertIn('<pre>Buyers billed: $', text)
        self.assertIn('\nPublishers due: $', text)
        self.assertIn('\nClient margin: $', text)
        self.assertIn('\nAvortyx routing fees: $', text)
        self.assertTrue(text.split('<pre>')[1].split('</pre>')[0].count('\n') == 3)

    def test_availability_when_measured(self):
        self.call('completed')
        self.assertIn('Availability: 99.9% (1000 checks, 1 failed)', self.report(1000, 1))

    def test_a_day_with_no_calls_says_so(self):
        # The workspace took a call five days ago, so it is still reported on.
        self.call('completed')
        CallLog.objects.update(created_at=timezone.now() - timezone.timedelta(days=5))
        text = self.report()
        self.assertIn('<b>0</b> calls received', text)
        self.assertIn('<b>0</b> connected', text)
        self.assertNotIn('turned away', text)
        self.assertIn('No calls arrived', text)

    def test_dry_run_prints_it_without_the_markup(self):
        self.call('completed')
        out = StringIO()
        call_command('daily_status', '--dry-run', f'--date={self.day:%Y-%m-%d}', stdout=out)
        printed = out.getvalue()
        self.assertIn('▍ AVORTYX', printed)
        self.assertNotIn('<b>', printed)
        self.assertNotIn('<pre>', printed)


@override_settings(CACHES=CACHE, TELEGRAM_BOT_TOKEN='t')
class DailyStatusSendTests(TestCase):
    def reply(self, code, text=''):
        r = mock.Mock()
        r.status_code = code
        r.text = text
        return r

    def test_it_is_sent_as_html(self):
        with mock.patch('requests.post', return_value=self.reply(200)) as post:
            self.assertTrue(Command()._send('1', '<b>hi</b>'))
        self.assertEqual(post.call_args.kwargs['data']['parse_mode'], 'HTML')

    def test_a_formatting_refusal_falls_back_to_plain(self):
        replies = [self.reply(400, "can't parse entities"), self.reply(200)]
        with mock.patch('requests.post', side_effect=replies) as post:
            cmd = Command()
            cmd.stderr = StringIO()
            self.assertTrue(cmd._send('1', '<b>Total</b> 5 &amp; more'))
        second = post.call_args_list[1].kwargs['data']
        self.assertNotIn('parse_mode', second)
        self.assertEqual(second['text'], 'Total 5 & more')

    def test_any_other_refusal_is_not_retried(self):
        with mock.patch('requests.post', return_value=self.reply(403, 'bot was blocked')) as post:
            cmd = Command()
            cmd.stderr = StringIO()
            self.assertFalse(cmd._send('1', 'x'))
        self.assertEqual(post.call_count, 1)
