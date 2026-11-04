"""CH-564: a deleted destination leaves no line behind in any campaign.

Run:  python manage.py test routing.test_orphan_lines
"""
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import ActivityLog, Organization, User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from routing import orphan_lines as O
from routing.models import CallLog, RoutingRule, RuleDestination

TFN = '+18885480775'
OTHER = '+18443123936'


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class Base(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.user = User.objects.create(username='mike', email='mike@x.io', organization=self.org,
                                        role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}')
        self.crm = Buyer.objects.create(organization=self.org, name='CRM')
        self.q15 = Buyer.objects.create(organization=self.org, name='Q15')
        self.rules = []
        for name in ('C-02', 'C-03', 'C-07'):
            c = Campaign.objects.create(organization=self.org, name=name)
            self.rules.append(RoutingRule.objects.create(organization=self.org, campaign=c, name=name,
                                                         rule_type='priority', priority=1))
        self.dest = Destination.objects.create(organization=self.org, buyer=self.crm, name='CRM|0775|',
                                               tfn=TFN, enabled=True)
        Destination.objects.create(organization=self.org, buyer=self.crm, name='CRM|3936|', tfn=OTHER)
        Destination.objects.create(organization=self.org, buyer=self.q15, name='Q15|0775|', tfn=TFN)
        for rule in self.rules:
            # Written without the + in one campaign: numbers compare by digits.
            RuleDestination.objects.create(rule=rule, buyer=self.crm, priority=1, weight=75,
                                           destination=TFN if rule.name != 'C-03' else TFN[1:])
            RuleDestination.objects.create(rule=rule, buyer=self.crm, priority=2, destination=OTHER)
            RuleDestination.objects.create(rule=rule, buyer=self.q15, priority=3, destination=TFN)

    def crm_lines(self, number):
        return [l for l in RuleDestination.objects.filter(buyer=self.crm) if O._d10(l.destination) == O._d10(number)]

    def delete_on_portal(self, dest):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.api.delete(f'/api/destinations/{dest.id}/')
        self.assertEqual(r.status_code, 200, r.content)


class OrphanLineTests(Base):
    def test_deleting_a_destination_removes_its_line_from_every_campaign(self):
        self.assertEqual(len(self.crm_lines(TFN)), 3)
        self.delete_on_portal(self.dest)
        self.assertEqual(self.crm_lines(TFN), [])
        # Its other lines, and another buyer's line on the same number, stay.
        self.assertEqual(len(self.crm_lines(OTHER)), 3)
        self.assertEqual(RuleDestination.objects.filter(buyer=self.q15).count(), 3)
        self.assertEqual(O.find(self.org.id), [])

    def test_every_removed_line_is_kept_in_the_activity_log(self):
        self.delete_on_portal(self.dest)
        entries = ActivityLog.objects.filter(metadata__target_type='campaign_destination',
                                             action=ActivityLog.Action.RECORD_DELETED)
        self.assertEqual(entries.count(), 3)
        e = entries.first()
        self.assertEqual(e.user, self.user)
        line = e.metadata['removed_line']
        self.assertEqual((line['buyer_name'], O._d10(line['destination']), line['priority'], line['weight']),
                         ('CRM', O._d10(TFN), '1', '75'))
        self.assertIn(line['campaign_name'], {'C-02', 'C-03', 'C-07'})
        self.assertTrue(e.metadata['reason'].startswith(O.DELETED))

    def test_calls_keep_their_number_buyer_and_money(self):
        line = self.crm_lines(TFN)[0]
        call = CallLog.objects.create(organization=self.org, campaign=line.rule.campaign, buyer=self.crm,
                                      rule_destination=line, destination_number=TFN, revenue=50,
                                      caller_number='+15551234567', called_number='+18888910343',
                                      twilio_call_sid='sid-1', status='completed')
        self.delete_on_portal(self.dest)
        call.refresh_from_db()
        self.assertEqual((call.destination_number, call.buyer_id, call.revenue, call.rule_destination_id),
                         (TFN, self.crm.id, 50, None))

    def test_a_second_row_for_the_same_number_keeps_the_lines(self):
        Destination.objects.create(organization=self.org, buyer=self.crm, name='CRM|0775| b', tfn=TFN[1:])
        self.delete_on_portal(self.dest)
        self.assertEqual(len(self.crm_lines(TFN)), 3)

    def test_any_delete_path_removes_the_lines(self):
        with self.captureOnCommitCallbacks(execute=True):
            Destination.objects.filter(pk=self.dest.pk).delete()
        self.assertEqual(self.crm_lines(TFN), [])


class OrphanLinesCommandTests(Base):
    def leftovers(self):
        """The 5 Nov state: rows gone, lines left (before CH-564)."""
        from django.db.models.signals import post_delete
        post_delete.disconnect(sender=Destination, dispatch_uid='ch564-destination-lines')
        try:
            # Deleted on the portal: the activity log has it.
            with self.captureOnCommitCallbacks(execute=True):
                self.api.delete(f'/api/destinations/{self.dest.id}/')
            # Never on the page: no delete on record.
            Destination.objects.filter(buyer=self.crm, tfn=OTHER).delete()
            ActivityLog.objects.filter(pk__in=[a.pk for a in ActivityLog.objects.all()
                                               if '3936' in str((a.metadata or {}).get('target_name'))]).delete()
        finally:
            O.connect()
        # A deleted (archived) buyer's lines.
        old = Buyer.objects.create(organization=self.org, name='NEO (deleted 2026-11-03)', status='archived')
        RuleDestination.objects.create(rule=self.rules[0], buyer=old, destination='+18334094259')

    def run_cmd(self, *args):
        out = StringIO()
        call_command('orphan_lines', *args, stdout=out)
        return out.getvalue()

    def test_list_changes_nothing_and_says_why(self):
        self.leftovers()
        before = RuleDestination.objects.count()
        out = self.run_cmd()
        self.assertEqual(RuleDestination.objects.count(), before)
        self.assertIn('not on the Destinations page: 7', out)
        self.assertIn(f'{O.DELETED}: 3', out)
        self.assertIn(f'{O.BUYER_DELETED}: 1', out)
        self.assertIn(f'{O.NEVER_LISTED}: 3', out)
        self.assertIn('by mike@x.io', out)
        self.assertIn('nothing changed', out)

    def test_remove_takes_deleted_ones_only_unless_asked(self):
        self.leftovers()
        self.run_cmd('--remove', '--by', 'mike@x.io')
        left = O.find(self.org.id)
        self.assertEqual({O._d10(l.destination) for l in left}, {O._d10(OTHER)})
        self.assertEqual(ActivityLog.objects.filter(metadata__reason='orphan_lines (CH-564)').count(), 4)
        self.run_cmd('--remove', '--by', 'mike@x.io', '--include-never-listed')
        self.assertEqual(O.find(self.org.id), [])
        # The Q15 line on the same number was never an orphan.
        self.assertEqual(RuleDestination.objects.filter(buyer=self.q15).count(), 3)

    def test_remove_needs_a_name(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.run_cmd('--remove')
