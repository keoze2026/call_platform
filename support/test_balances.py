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


@override_settings(MAIN_ORGANIZATION_SLUG='interns', TWILIO_ACCOUNT_SID='AC1', TWILIO_AUTH_TOKEN='secret-token',
                   TELNYX_API_KEY='KEY-secret', ASSEMBLYAI_API_KEY='aai')
class BalancesTests(TestCase):
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
