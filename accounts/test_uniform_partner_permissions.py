"""CH-323: every publisher and buyer gets the same options."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from accounts.models import Organization
from accounts.partner_permissions import clean_permissions


class UniformTests(TestCase):
    def setUp(self):
        from buyers.models import Buyer
        from publishers.models import Publisher
        org = Organization.objects.create(name='O', slug='o')
        self.p_new = Publisher.objects.create(organization=org, name='New')
        self.p_off = Publisher.objects.create(organization=org, name='Off',
                                              permissions={'block_numbers': False, 'tfn_test': False,
                                                           'audio_recording': True})
        self.b = Buyer.objects.create(organization=org, name='B', permissions={'block_numbers': False})

    def test_new_partners_start_with_block_numbers_and_tfn_test(self):
        perms = clean_permissions({})
        self.assertTrue(perms['block_numbers'])
        self.assertTrue(perms['tfn_test'])

    def test_look_only_changes_nothing(self):
        out = StringIO()
        call_command('uniform_partner_permissions', stdout=out)
        self.p_off.refresh_from_db()
        self.assertFalse(self.p_off.permissions['block_numbers'])
        self.assertIn('would switch on for 2 publisher(s)', out.getvalue())

    def test_apply_switches_them_on_and_keeps_the_rest(self):
        call_command('uniform_partner_permissions', '--apply', stdout=StringIO())
        for obj in (self.p_new, self.p_off):
            obj.refresh_from_db()
            self.assertTrue(obj.permissions['block_numbers'])
            self.assertTrue(obj.permissions['tfn_test'])
        self.p_off.refresh_from_db()
        self.assertTrue(self.p_off.permissions['audio_recording'])     # untouched
        self.b.refresh_from_db()
        self.assertTrue(self.b.permissions['block_numbers'])
        out = StringIO()
        call_command('uniform_partner_permissions', '--apply', stdout=out)   # again: nothing left
        self.assertIn('switched on for 0 publisher(s)', out.getvalue())
