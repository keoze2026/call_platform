"""CH-632 (customer accounts point 6, in the database): a routing link never
joins two accounts.

The rule itself is a PostgreSQL trigger (routing/same_account.py); the test
database is SQLite, so the trigger is proven on a copy of live and on live
with `python manage.py same_account --prove`. Here: the list of links matches
the models, the SQL is safe for the migration runner, and the check finds a
link between accounts (which only SQLite still lets us store).

Run:  python manage.py test routing.test_same_account
"""
from io import StringIO
from unittest import skipUnless

from django.apps import apps
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from accounts.models import Organization
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing import same_account
from routing.same_account import LINKS, cross_links, remove_sql, tables, trigger_sql


def _model(table):
    return next(m for m in apps.get_models() if m._meta.db_table == table)


class LinkListTests(TestCase):
    def test_every_link_is_a_real_foreign_key_between_two_account_tables(self):
        for child, col, parent in LINKS:
            model = _model(child)
            field = next((f for f in model._meta.fields if f.is_relation and f.column == col), None)
            self.assertIsNotNone(field, f'{child}.{col}')
            self.assertEqual(field.related_model._meta.db_table, parent, f'{child}.{col}')
            for m in (model, field.related_model):
                self.assertIn('organization_id', [f.column for f in m._meta.fields], m._meta.db_table)

    def test_the_routing_chain_is_covered(self):
        covered = {(c, col) for c, col, _ in LINKS}
        for link in (('phone_numbers', 'campaign_id'), ('routing_rules', 'campaign_id'),
                     ('rule_destinations', 'buyer_id'), ('destinations', 'buyer_id')):
            self.assertIn(link, covered)

    def test_call_records_are_not_guarded(self):
        # A call is never refused for being saved (the CH-555 lesson).
        self.assertNotIn('call_logs', tables())


class SqlTests(TestCase):
    def test_no_percent_sign_reaches_the_migration_runner(self):
        # The driver reads every % as a placeholder (CH-581 fix).
        for stmt in trigger_sql() + remove_sql():
            self.assertNotIn('%', stmt)

    def test_one_trigger_per_table_and_removal_undoes_each(self):
        sql = '\n'.join(trigger_sql())
        undo = '\n'.join(remove_sql())
        for table in tables():
            name = same_account.trigger_name(table)
            self.assertIn(f'create trigger {name} before insert or update on {table} ', sql)
            self.assertIn(f'drop trigger if exists {name} on {table}', undo)
            self.assertIn(f'drop function if exists {name}()', undo)

    def test_existing_rows_are_checked_only_when_a_link_or_account_changes(self):
        fn = '\n'.join(trigger_sql())
        self.assertIn('NEW.campaign_id is distinct from OLD.campaign_id', fn)
        self.assertIn('NEW.organization_id is distinct from OLD.organization_id', fn)


class CheckTests(TestCase):
    def setUp(self):
        self.ours = Organization.objects.create(name='Interns', slug='interns')
        self.theirs = Organization.objects.create(name='Texora', slug='texora')
        self.campaign = Campaign.objects.create(organization=self.ours, name='OURS')
        self.foreign = Campaign.objects.create(organization=self.theirs, name='THEIRS')
        self.tfn = PhoneNumber.objects.create(organization=self.ours, number='+18005550101',
                                              campaign=self.campaign)

    def counts(self):
        with connection.cursor() as c:
            return {(t, col): n for t, col, _, n in cross_links(c)}

    def test_nothing_crosses_when_links_stay_in_their_account(self):
        self.assertEqual(sum(self.counts().values()), 0)

    @skipUnless(connection.vendor == 'sqlite', 'PostgreSQL refuses to store the link')
    def test_a_link_into_another_account_is_found(self):
        PhoneNumber.objects.filter(id=self.tfn.id).update(campaign=self.foreign)
        counts = self.counts()
        self.assertEqual(counts[('phone_numbers', 'campaign_id')], 1)
        self.assertEqual(sum(counts.values()), 1)

    def test_the_command_reports_and_changes_nothing(self):
        out = StringIO()
        call_command('same_account', '--prove', stdout=out)
        text = out.getvalue()
        self.assertIn('total crossing accounts: 0', text)
        if connection.vendor != 'postgresql':
            self.assertIn('--prove needs PostgreSQL', text)
        self.assertEqual(PhoneNumber.objects.get(id=self.tfn.id).campaign_id, self.campaign.id)
        self.assertEqual(Campaign.objects.filter(organization=self.theirs).count(), 1)

    @skipUnless(connection.vendor == 'postgresql', 'the rule is a PostgreSQL trigger')
    def test_postgresql_refuses_the_link(self):
        with self.assertRaises(DatabaseError), transaction.atomic():
            PhoneNumber.objects.filter(id=self.tfn.id).update(campaign=self.foreign)
