"""CH-201: switching a line on or off never touches any campaign's routing.

Run:  python manage.py test buyers.test_line_edit_routing
"""
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from routing.models import RoutingRule, RuleDestination

PDSO, CRUG, PDSO2 = '+18667404516', '+18779642078', '+18667400000'


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class LineEditRoutingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.user = User.objects.create(username='m', email='m@x.io', organization=self.org,
                                        role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}')
        self.pdso = Buyer.objects.create(organization=self.org, name='PDSO')
        self.crug = Buyer.objects.create(organization=self.org, name='Crug')
        self.pdso_line = Destination.objects.create(organization=self.org, buyer=self.pdso, name='PDSO', tfn=PDSO)
        self.pdso_line2 = Destination.objects.create(organization=self.org, buyer=self.pdso, name='PDSO 2', tfn=PDSO2)
        Destination.objects.create(organization=self.org, buyer=self.crug, name='CrugTFN', tfn=CRUG)
        self.c03 = self.route('C-03', PDSO, self.pdso)
        self.c06 = self.route('C-06', CRUG, self.crug)
        self.c09 = self.route('C-09', PDSO2, self.pdso)

    def route(self, name, number, buyer):
        camp = Campaign.objects.create(organization=self.org, name=name)
        rule = RoutingRule.objects.create(organization=self.org, campaign=camp, name=name,
                                          rule_type='priority')
        return RuleDestination.objects.create(rule=rule, destination=number, buyer=buyer)

    def patch(self, line, body):
        r = self.api.patch(f'/api/destinations/{line.id}/', body, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)

    def numbers(self):
        for rd in (self.c03, self.c06, self.c09):
            rd.refresh_from_db()
        return self.c03.destination, self.c06.destination, self.c09.destination

    def test_switching_a_line_off_and_on_changes_no_routing(self):
        self.patch(self.pdso_line, {'enabled': False})
        self.patch(self.pdso_line, {'enabled': True})
        self.patch(self.pdso_line2, {'enabled': False})
        self.assertEqual(self.numbers(), (PDSO, CRUG, PDSO2))

    def test_another_line_of_the_same_buyer_keeps_its_own_routes(self):
        self.patch(self.pdso_line2, {'concurrency_cap': 50})
        self.assertEqual(self.numbers(), (PDSO, CRUG, PDSO2))

    def test_a_new_number_moves_only_the_routes_on_the_old_number(self):
        self.patch(self.pdso_line, {'tfn': '+18665550123'})
        self.assertEqual(self.numbers(), ('+18665550123', CRUG, PDSO2))
