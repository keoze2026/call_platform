"""CH-503: every campaign line gets a Destinations row, off unless asked.

Run:  python manage.py test routing.test_list_campaign_lines
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from routing.models import RoutingRule, RuleDestination


class ListCampaignLinesTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.q06 = Buyer.objects.create(organization=self.org, name='Q06')
        self.bub = Buyer.objects.create(organization=self.org, name='BUB')
        self.q04 = Buyer.objects.create(organization=self.org, name='Q04')
        Destination.objects.create(organization=self.org, buyer=self.q04, name='Q04|2494|',
                                   tfn='+18779122494', enabled=True)
        for name in ('C-02', 'C-03'):
            c = Campaign.objects.create(organization=self.org, name=name, status='active')
            r = RoutingRule.objects.create(organization=self.org, campaign=c, name=name,
                                           rule_type='priority', priority=1)
            RuleDestination.objects.create(rule=r, destination='+18666744220', buyer=self.q06)
            RuleDestination.objects.create(rule=r, destination='18884033432', buyer=self.bub)
            RuleDestination.objects.create(rule=r, destination='+18779122494', buyer=self.q04)
        old = Campaign.objects.create(organization=self.org, name='Old', status='archived')
        r = RoutingRule.objects.create(organization=self.org, campaign=old, name='old', rule_type='priority')
        RuleDestination.objects.create(rule=r, destination='+18005550000', buyer=self.q06)

    def run_cmd(self, *args):
        out = StringIO()
        call_command('list_campaign_lines', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        out = self.run_cmd()
        self.assertIn('2 Destinations rows to add', out)
        self.assertIn('Q06|4220|', out)
        self.assertEqual(Destination.objects.count(), 1)

    def test_apply_adds_one_row_per_buyer_and_number_switched_off(self):
        self.run_cmd('--apply')
        rows = {(d.buyer.name, d.name, d.tfn, d.enabled) for d in Destination.objects.exclude(buyer=self.q04)}
        self.assertEqual(rows, {('Q06', 'Q06|4220|', '+18666744220', False),
                                ('BUB', 'BUB|3432|', '+18884033432', False)})
        # Listed already: untouched. Archived campaign: left out.
        self.assertEqual(Destination.objects.filter(buyer=self.q04).count(), 1)
        self.assertFalse(Destination.objects.filter(tfn__contains='8005550000').exists())

    def test_on_list_adds_those_switched_on(self):
        self.run_cmd('--apply', '--on', '8884033432')
        self.assertTrue(Destination.objects.get(buyer=self.bub).enabled)
        self.assertFalse(Destination.objects.get(buyer=self.q06).enabled)

    def test_second_run_adds_nothing(self):
        self.run_cmd('--apply')
        self.assertIn('0 Destinations rows to add', self.run_cmd('--apply'))

    def test_line_with_an_off_row_routes_nowhere(self):
        # The row added off stops the line, as a paused destination does.
        from routing.engine import RoutingEngine
        self.run_cmd('--apply')
        self.assertFalse(RoutingEngine.is_destination_enabled(self.q06, '+18666744220'))
