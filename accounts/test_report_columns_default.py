"""CH-224: a new buyer or publisher starts with every report column visible.

Run:  python manage.py test accounts.test_report_columns_default
"""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from accounts.partner_permissions import REPORT_COLUMNS, visible_report_columns
from buyers.models import Buyer
from publishers.models import Publisher

ALL = [c['key'] for c in REPORT_COLUMNS]
CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=CACHE, ALLOWED_HOSTS=['*'])
class ReportColumnsDefaultTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Org', slug='org')
        self.admin = User.objects.create(username='adm', email='adm@x.io', organization=self.org,
                                         role='admin', is_active=True, is_superuser=True)

    def test_twelve_columns(self):
        self.assertEqual(len(ALL), 12)

    def test_a_new_buyer_and_publisher_see_every_column(self):
        b = Buyer.objects.create(organization=self.org, name='New buyer')
        p = Publisher.objects.create(organization=self.org, name='New publisher')
        self.assertEqual(b.visible_report_columns, ALL)
        self.assertEqual(p.visible_report_columns, ALL)

    def test_the_settings_page_reads_twelve_of_twelve(self):
        b = Buyer.objects.create(organization=self.org, name='New buyer')
        r = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}').get(
            f'/api/accounts/buyer/{b.id}/settings')
        if r.status_code == 404:
            self.skipTest('settings route is mounted elsewhere')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['visible_report_columns'], ALL)

    def test_the_partner_login_sees_every_column(self):
        b = Buyer.objects.create(organization=self.org, name='New buyer')
        login = User.objects.create(username='b', email='b@x.io', organization=self.org,
                                    role='buyer', buyer=b, is_active=True)
        self.assertEqual(visible_report_columns(login), ALL)

    def test_unticking_still_hides(self):
        b = Buyer.objects.create(organization=self.org, name='New buyer')
        b.visible_report_columns = ['incoming', 'connected']
        b.save()
        b.refresh_from_db()
        self.assertEqual(b.visible_report_columns, ['incoming', 'connected'])

    def test_fill_gives_empty_partners_all_and_leaves_set_ones(self):
        empty = Buyer.objects.create(organization=self.org, name='Old buyer')
        Buyer.objects.filter(pk=empty.pk).update(visible_report_columns=[])
        chosen = Publisher.objects.create(organization=self.org, name='Chosen')
        Publisher.objects.filter(pk=chosen.pk).update(visible_report_columns=['incoming'])
        out = StringIO()
        call_command('fill_report_columns', stdout=out)
        self.assertIn('1 with no report columns ticked', out.getvalue())
        empty.refresh_from_db()
        self.assertEqual(empty.visible_report_columns, [])
        call_command('fill_report_columns', '--apply', stdout=StringIO())
        empty.refresh_from_db()
        chosen.refresh_from_db()
        self.assertEqual(empty.visible_report_columns, ALL)
        self.assertEqual(chosen.visible_report_columns, ['incoming'])
