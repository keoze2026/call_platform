"""Risk shown for a carrier's numbers (CH-135, CH-141)."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from accounts.models import Organization
from phone_numbers.models import Carrier, PhoneNumber
from phone_numbers.services import PhoneNumberService


class CarrierRiskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Org')

    def carrier(self, code, **kw):
        return Carrier.objects.create(organization=self.org, name=code, code=code, **kw)

    def number(self, carrier, digits='18005550001'):
        return PhoneNumber.objects.create(
            organization=self.org, number='+' + digits, carrier=carrier, vendor='other')

    def risk_of(self, number):
        return PhoneNumberService.format_number(number, live_calls_count=0)['risk']

    def test_auto_follows_the_expiry_rules(self):
        self.assertEqual(self.carrier('A', single_use=True).risk, 'high')
        self.assertEqual(self.carrier('B', lifetime_hours=24).risk, 'high')
        self.assertEqual(self.carrier('C', max_answered_calls=10).risk, 'high')
        self.assertEqual(self.carrier('D').risk, 'low')

    def test_a_set_risk_wins_over_the_rules(self):
        # HYU XOLO: no expiry rule, High by the owner's definition.
        self.assertEqual(self.carrier('HYU', risk_override='high').risk, 'high')
        # A carrier with expiry rules can still be marked Low.
        self.assertEqual(self.carrier('E', lifetime_hours=24, risk_override='low').risk, 'low')

    def test_numbers_show_their_carriers_risk(self):
        hyu = self.carrier('HYU', risk_override='high')
        xlr = self.carrier('XLR')
        kmq = self.carrier('KMQ', lifetime_hours=24, max_answered_calls=10)
        self.assertEqual(self.risk_of(self.number(hyu, '18005550002')), 'high')
        self.assertEqual(self.risk_of(self.number(xlr, '18005550003')), 'low')
        self.assertEqual(self.risk_of(self.number(kmq, '18005550004')), 'high')

    def test_a_number_without_a_carrier_is_low(self):
        n = PhoneNumber.objects.create(organization=self.org, number='+18005550005', vendor='other')
        self.assertEqual(self.risk_of(n), 'low')

    def test_add_carrier_sets_and_clears_risk(self):
        out = StringIO()
        call_command('add_carrier', name='XOLO', code='HYU', risk='high', stdout=out)
        c = Carrier.objects.get(code='HYU')
        self.assertEqual((c.risk_override, c.risk), ('high', 'high'))
        call_command('add_carrier', name='XOLO', code='HYU', risk='auto', stdout=out)
        c.refresh_from_db()
        self.assertEqual((c.risk_override, c.risk), ('', 'low'))

    def test_add_carrier_leaves_risk_alone_when_not_given(self):
        call_command('add_carrier', name='XOLO', code='HYU', risk='high', stdout=StringIO())
        call_command('add_carrier', name='XOLO', code='HYU', stdout=StringIO())
        self.assertEqual(Carrier.objects.get(code='HYU').risk_override, 'high')
