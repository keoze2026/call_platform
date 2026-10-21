"""CH-194: a paused TFN stays on the list (campaign and Numbers page).

Run:  python manage.py test phone_numbers.test_paused_listed
"""
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from phone_numbers.services import PhoneNumberService


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class PausedNumbersStayListedTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(username='owner', email='owner@x.io', organization=self.org,
                                         role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.camp = Campaign.objects.create(organization=self.org, name='PDSO')
        self.n = 0

    def number(self, status='active'):
        self.n += 1
        return PhoneNumber.objects.create(organization=self.org, number=f'+1855202{self.n:04d}',
                                          vendor='Other', campaign=self.camp, status=status)

    def listed(self):
        return {n.number for n in PhoneNumberService.list_numbers(self.owner)}

    def test_pausing_a_number_keeps_it_on_its_campaign(self):
        n = self.number()
        r = self.api.patch(f'/api/numbers/{n.id}', {'status': 'paused'}, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        n.refresh_from_db()
        self.assertEqual((n.status, n.campaign_id), ('paused', self.camp.id))
        self.assertIn(n.number, self.listed())

    def test_the_list_endpoint_returns_paused_numbers(self):
        n = self.number('paused')
        r = self.api.get('/api/numbers/?page_size=200')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn(n.number, str(r.content))

    def test_a_deleted_number_stays_off_the_list(self):
        active, paused, gone = self.number(), self.number('paused'), self.number('released')
        self.assertEqual(self.listed(), {active.number, paused.number})
