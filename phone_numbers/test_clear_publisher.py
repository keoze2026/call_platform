"""CH-192: the publisher on a tracking number can be taken off again.

Run:  python manage.py test phone_numbers.test_clear_publisher
"""
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class ClearPublisherTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(username='owner', email='owner@x.io', organization=self.org,
                                         role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO 2')
        self.num = PhoneNumber.objects.create(organization=self.org, number='+18552026099',
                                              vendor='Other', publisher=self.pub, label='Old name')

    def patch(self, body):
        r = self.api.patch(f'/api/numbers/{self.num.id}', body, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.num.refresh_from_db()
        return r.json()

    def test_null_takes_the_publisher_off(self):
        self.patch({'publisher_id': None})
        self.assertIsNone(self.num.publisher_id)

    def test_empty_takes_the_publisher_off(self):
        self.patch({'publisher_id': ''})
        self.assertIsNone(self.num.publisher_id)

    def test_leaving_it_out_keeps_the_publisher(self):
        self.patch({'label': 'New name'})
        self.assertEqual((self.num.publisher_id, self.num.label), (self.pub.id, 'New name'))

    def test_an_empty_name_clears_the_name(self):
        self.patch({'label': ''})
        self.assertEqual(self.num.label, '')

    def test_a_publisher_can_still_be_set(self):
        other = Publisher.objects.create(organization=self.org, name='Other')
        self.patch({'publisher_id': None})
        self.patch({'publisher_id': str(other.id)})
        self.assertEqual(self.num.publisher_id, other.id)
