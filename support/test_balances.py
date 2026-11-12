"""CH-670: manage.py balances - every connected service's balance, read-only.

Run:  python manage.py test support.test_balances
"""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import Organization
from billing.models import BillingAccount


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self.data


def fake_get(url, **kw):
    if 'twilio.com' in url:
        return Resp({'balance': '182.45', 'currency': 'USD'})
    if 'telnyx.com' in url:
        return Resp({'data': {'balance': '40.10', 'available_credit': '40.10', 'currency': 'USD'}})
    return Resp({}, 404)


KEYS = {'TWILIO_ACCOUNT_SID': 'AC1', 'TWILIO_AUTH_TOKEN': 'secret-token', 'TELNYX_API_KEY': 'KEY-secret',
        'ASSEMBLYAI_API_KEY': 'aai'}


@override_settings(MAIN_ORGANIZATION_SLUG='interns')
class BalancesTests(TestCase):
    def setUp(self):
        # The server's own .env is in the test container: only these keys count.
        p = mock.patch('support.management.commands.balances._key', side_effect=lambda n: KEYS.get(n, ''))
        p.start()
        self.addCleanup(p.stop)

    def run_it(self):
        out = StringIO()
        with mock.patch('requests.get', side_effect=fake_get), \
                mock.patch('support.system_status.subscriptions',
                           return_value={'carrier_balance': {'amount': 1267.41, 'updated': '2026-11-11'}}):
            call_command('balances', stdout=out)
        return out.getvalue()

    def test_each_service_one_line_and_no_key_printed(self):
        org = Organization.objects.create(name='Avortyx', slug='interns')
        BillingAccount.objects.create(organization=org, balance=Decimal('4225'))
        out = self.run_it()
        self.assertIn('182.45 USD', out)
        self.assertIn('40.10 USD (available 40.10 USD)', out)
        self.assertIn('$1,267.41 - XOLO has no API', out)
        self.assertIn('$4,225.00 (our workspace in the portal)', out)
        self.assertIn('not connected (no DEEPGRAM_API_KEY)', out)
        self.assertRegex(out, r'AssemblyAI \(transcripts\)\s+connected - no balance API')
        self.assertIn('Freedom (carrier)', out)
        for secret in ('secret-token', 'KEY-secret', 'aai '):
            self.assertNotIn(secret, out)

    def test_a_service_that_fails_does_not_hide_the_others(self):
        with mock.patch('support.management.commands.balances.twilio', side_effect=ConnectionError('down')):
            out = self.run_it()
        self.assertIn('error: ConnectionError: down', out)
        self.assertIn('40.10 USD', out)


def low_telnyx(url, **kw):
    if 'twilio.com' in url:
        return Resp({'balance': '252.93', 'currency': 'USD'})
    return Resp({'data': {'balance': '8.23', 'available_credit': '8.23', 'currency': 'USD'}})


@override_settings(BALANCE_CHECKS=True)
class LowBalanceAlertTests(TestCase):
    """12 Nov: "low alerts to the Notifications group, since this might cost
    us - but rarely, not regularly". Telnyx was $8.23."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        p = mock.patch('support.management.commands.balances._key', side_effect=lambda n: KEYS.get(n, ''))
        p.start()
        self.addCleanup(p.stop)

    def watch(self, getter=low_telnyx, at=None):
        import time

        from support import system_status as S
        sent = []
        with mock.patch('requests.get', side_effect=getter), \
                mock.patch.object(S, 'collect', lambda: [('8', S.check_provider_balances())]), \
                mock.patch('support.system_status.time.time', return_value=at or time.time()):
            S.watch(sent.append)
        return sent

    def test_low_is_said_once_a_day_and_back_to_normal_once(self):
        import time

        from django.core.cache import cache
        now = time.time()
        sent = self.watch(at=now)
        self.assertEqual(len(sent), 1)
        self.assertIn('Telnyx balance: $8.23 · below $10 - please top up', sent[0])
        self.assertEqual(self.watch(at=now + 900), [])            # every 15 min: nothing new
        self.assertEqual(self.watch(at=now + 12 * 3600), [])      # not twice a day
        self.assertEqual(len(self.watch(at=now + 25 * 3600)), 1)  # once a day while low
        cache.delete('status:balance-read:telnyx')
        topped = lambda url, **kw: Resp({'data': {'balance': '60', 'currency': 'USD'}}) if 'telnyx' in url \
            else low_telnyx(url)
        back = self.watch(topped, at=now + 26 * 3600)
        self.assertEqual(len(back), 1)
        self.assertIn('Back to normal', back[0])

    def test_twilio_above_its_floor_is_quiet_and_a_failed_read_never_alerts(self):
        from support import system_status as S
        with mock.patch('requests.get', side_effect=low_telnyx):
            items = {i.name: i for i in S.check_provider_balances()}
        self.assertEqual((items['Twilio balance'].status, items['Twilio balance'].detail), (S.OK, '$252.93'))
        self.assertEqual(items['Telnyx balance'].status, S.WARN)
        from django.core.cache import cache
        cache.clear()
        with mock.patch('requests.get', side_effect=ConnectionError('down')):
            items = S.check_provider_balances()
        self.assertEqual({i.status for i in items}, {S.NA})
        self.assertEqual(self.watch(lambda url, **kw: (_ for _ in ()).throw(ConnectionError('down'))), [])

    @override_settings(BALANCE_CHECKS=False)
    def test_off_in_the_test_suite(self):
        from support import system_status as S
        self.assertEqual(S.check_provider_balances(), [])
