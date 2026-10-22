"""CH-200: who switched a buyer line on or off is recorded and shown.

Run:  python manage.py test buyers.test_destination_switches
"""
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import ActivityLog, Organization, User
from buyers.destination import Destination
from buyers.models import Buyer


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class DestinationSwitchTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.user = User.objects.create(username='mike', email='mike@x.io', first_name='Mike',
                                        last_name='Ross', organization=self.org, role='admin',
                                        is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}',
                          REMOTE_ADDR='41.90.1.2')
        self.buyer = Buyer.objects.create(organization=self.org, name='PDSO')
        self.line = Destination.objects.create(organization=self.org, buyer=self.buyer, name='PDSO',
                                               tfn='+18667404516')

    def switch(self, on):
        r = self.api.patch(f'/api/destinations/{self.line.id}/', {'enabled': on},
                           content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_switching_off_is_recorded_with_who_and_which_line(self):
        out = self.switch(False)
        entry = ActivityLog.objects.filter(metadata__target_type='destination').order_by('-created_at').first()
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.metadata['switched'], 'off')
        self.assertEqual(entry.metadata['target_name'], 'PDSO +18667404516')
        self.assertEqual(entry.ip_address, '41.90.1.2')
        self.assertEqual(out['last_switched']['enabled'], False)
        self.assertEqual(out['last_switched']['by'], 'Mike Ross')

    def test_the_list_shows_the_latest_switch(self):
        self.switch(False)
        self.switch(True)
        r = self.api.get('/api/destinations/')
        self.assertEqual(r.status_code, 200, r.content)
        row = next(d for d in r.json()['items'] if d['id'] == str(self.line.id))
        self.assertEqual((row['last_switched']['enabled'], row['last_switched']['by_email']), (True, 'mike@x.io'))
        self.assertEqual(ActivityLog.objects.filter(metadata__switched='on').count(), 1)

    def test_a_line_never_switched_shows_nothing(self):
        r = self.api.get(f'/api/destinations/{self.line.id}/')
        self.assertIsNone(r.json()['last_switched'])

    def test_other_edits_are_not_a_switch(self):
        r = self.api.patch(f'/api/destinations/{self.line.id}/', {'concurrency_cap': 50},
                           content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json()['last_switched'])
