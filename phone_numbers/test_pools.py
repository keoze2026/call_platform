"""Customer accounts point 5: TFNs kept separate.

The database trigger is tested on PostgreSQL (`manage.py tfn_pools --prove`);
these are the same rules on save, and the release paths.

Run:  python manage.py test phone_numbers.test_pools
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization, User
from phone_numbers import pools
from phone_numbers.lifecycle import retire
from phone_numbers.models import PhoneNumber
from phone_numbers.services import PhoneNumberService


@override_settings(MAIN_ORGANIZATION_SLUG='interns', TFN_QUARANTINE_DAYS=30)
class PoolTests(TestCase):
    def setUp(self):
        self.main = Organization.objects.create(name='Avortyx', slug='interns')
        self.cust = Organization.objects.create(name='Test A', slug='test-a')
        self.other = Organization.objects.create(name='Test B', slug='test-b')

    def tfn(self, org, number):
        return PhoneNumber.objects.create(organization=org, number=number, number_type='toll_free')

    def test_our_tfns_are_main_and_a_customer_tfn_is_inventory(self):
        self.assertEqual(self.tfn(self.main, '+18880000001').pool, pools.MAIN)
        self.assertEqual(self.tfn(self.cust, '+18880000002').pool, pools.INVENTORY)

    def test_one_of_our_tfns_can_never_go_to_a_customer(self):
        n = self.tfn(self.main, '+18880000001')
        n.organization = self.cust
        with self.assertRaisesMessage(pools.PoolError, 'main account only'):
            n.save()
        self.assertEqual(PhoneNumber.objects.get(pk=n.pk).organization_id, self.main.id)

    def test_one_of_our_tfns_never_leaves_the_main_pool(self):
        n = self.tfn(self.main, '+18880000001')
        for pool in (pools.INVENTORY, pools.QUARANTINE):
            n.pool = pool
            with self.assertRaises(pools.PoolError):
                n.save()

    def test_a_customer_tfn_can_never_be_made_main(self):
        n = self.tfn(self.cust, '+18880000002')
        n.pool = pools.MAIN
        with self.assertRaisesMessage(pools.PoolError, 'main account only'):
            n.save()

    def test_a_customer_tfn_never_moves_to_another_account(self):
        n = self.tfn(self.cust, '+18880000002')
        n.organization = self.other
        with self.assertRaisesMessage(pools.PoolError, 'only once, from the stock to one customer'):
            n.save()

    def test_a_customer_release_goes_to_trash_for_good(self):
        # CH-665 (Sir, 11 Nov): "Once a TFN is retired it goes to trash. Never
        # merge any customer details to anyone ever."
        u = User.objects.create(username='c@x.io', email='c@x.io', organization=self.cust, role='admin')
        n = self.tfn(self.cust, '+18880000002')
        PhoneNumberService.release_number(str(n.id), u)
        n.refresh_from_db()
        self.assertEqual((n.status, n.pool, n.organization_id), ('released', pools.TRASH, self.cust.id))
        # Never again: not to another customer, not to us, not back in use.
        for org, pool in ((self.other, pools.INVENTORY), (self.main, pools.MAIN), (self.cust, pools.INVENTORY)):
            n = PhoneNumber.objects.get(pk=n.pk)
            n.organization, n.pool, n.status = org, pool, 'active'
            with self.assertRaises(pools.PoolError):
                n.save()
        self.assertEqual(PhoneNumber.objects.get(pk=n.pk).pool, pools.TRASH)

    def test_a_number_in_quarantine_before_ch665_is_never_reused_either(self):
        n = self.tfn(self.cust, '+18880000002')
        PhoneNumber.objects.filter(pk=n.pk).update(pool=pools.QUARANTINE,
                                                   quarantine_until=timezone.now() - timedelta(days=1))
        n = PhoneNumber.objects.get(pk=n.pk)
        n.organization, n.pool = self.other, pools.INVENTORY
        with self.assertRaises(pools.PoolError):
            n.save()

    def test_our_release_stays_main(self):
        u = User.objects.create(username='m@x.io', email='m@x.io', organization=self.main, role='admin')
        n = self.tfn(self.main, '+18880000001')
        PhoneNumberService.release_number(str(n.id), u)
        n.refresh_from_db()
        self.assertEqual((n.status, n.pool, n.quarantine_until), ('released', pools.MAIN, None))

    def test_a_retired_customer_tfn_goes_to_trash(self):
        n = self.tfn(self.cust, '+18880000002')
        with mock.patch('phone_numbers.lifecycle._notify'):
            self.assertTrue(retire(n, 'used'))
        n.refresh_from_db()
        self.assertEqual(n.pool, pools.TRASH)

    def test_a_number_already_in_the_platform_cannot_be_imported_by_a_customer(self):
        self.tfn(self.main, '+18880000001')
        u = User.objects.create(username='c@x.io', email='c@x.io', organization=self.cust, role='admin')
        data = mock.Mock(phone_number='+18880000001', number_type='toll_free', campaign_id=None)
        with self.assertRaisesMessage(ValueError, 'already exists'):
            PhoneNumberService.import_existing_number(data, u)

    def test_the_migration_puts_every_existing_tfn_in_its_pool(self):
        from django.apps import apps
        a, b = self.tfn(self.main, '+18880000001'), self.tfn(self.cust, '+18880000002')
        PhoneNumber.objects.update(pool='')
        pools.fill_existing(apps, None)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.pool, b.pool), (pools.MAIN, pools.INVENTORY))

    def test_the_trigger_holds_the_same_rules(self):
        sql = '\n'.join(pools.trigger_sql('interns', 'tfn-stock'))
        for words in ('main account only', 'never leaves the main pool', 'only once, from the stock to one customer',
                      'a trashed TFN is never used again', 'stock account only', "slug = 'interns'",
                      "slug = 'tfn-stock'", 'security definer'):
            self.assertIn(words, sql)
        # The migration runner reads '%' as a parameter placeholder (rehearsal, 5 Nov).
        self.assertNotIn('%', sql)
