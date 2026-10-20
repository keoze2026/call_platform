"""CH-179: the "TFNs deleted" notice stays until the user clicks Seen.

Run:  python manage.py test notifications.test_deleted_tfns
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from phone_numbers.lifecycle import retire
from phone_numbers.models import Carrier, PhoneNumber

URL = '/api/notifications/deleted-tfns'


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class DeletedTfnNoticeTests(TestCase):
    def setUp(self):
        p = mock.patch('phone_numbers.lifecycle._notify')
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='interns')
        self.admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin')
        self.kmq = Carrier.objects.create(organization=self.org, name='Freedom', code='KMQ')
        self.n = 0

    def number(self):
        self.n += 1
        return PhoneNumber.objects.create(
            organization=self.org, number=f'+1833000{self.n:04d}', carrier=self.kmq,
            twilio_sid=f'TFN-{self.n}', status=PhoneNumber.Status.ACTIVE,
        )

    def client_for(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def notice(self, user=None):
        return self.client_for(user or self.admin).get(URL).json()

    def seen(self, user=None, **body):
        return self.client_for(user or self.admin).post(
            URL + '/seen', data=json.dumps(body), content_type='application/json').json()

    def test_a_used_number_shows_with_its_reason(self):
        retire(self.number(), 'used')
        data = self.notice()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['numbers'][0]['number'], '+18330000001')
        self.assertEqual(data['numbers'][0]['carrier_code'], 'KMQ')
        self.assertEqual(data['numbers'][0]['reason'], 'Used (single use)')

    def test_the_nightly_rule_reads_as_words(self):
        retire(self.number(), '12 answered calls on 2026-10-20, over the limit of 10, nightly 1:30 AM rule')
        self.assertIn('12 answered calls on 2026-10-20', self.notice()['numbers'][0]['reason'])

    def test_it_stays_until_seen(self):
        retire(self.number(), 'used')
        self.assertEqual(self.notice()['count'], 1)
        self.assertEqual(self.notice()['count'], 1)  # looking is not acknowledging
        after = self.seen()
        self.assertEqual(after['count'], 0)
        self.assertEqual(self.notice()['count'], 0)

    def test_a_new_retirement_brings_it_back(self):
        retire(self.number(), 'used')
        self.seen()
        retire(self.number(), 'expired')
        data = self.notice()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['numbers'][0]['reason'], 'Time limit reached')

    def test_seen_up_to_keeps_a_number_retired_after_the_notice_was_shown(self):
        retire(self.number(), 'used')
        shown = self.notice()['newest_at']
        later = self.number()
        retire(later, 'used')
        PhoneNumber.objects.filter(pk=later.pk).update(released_at=timezone.now() + timedelta(seconds=1))
        self.seen(up_to=shown)
        data = self.notice()
        self.assertEqual([x['number'] for x in data['numbers']], [later.number])

    def test_seen_is_per_user(self):
        other = User.objects.create(username='b', email='b@x.io', organization=self.org, role='admin')
        retire(self.number(), 'used')
        self.seen()
        self.assertEqual(self.notice(other)['count'], 1)

    def test_a_number_deleted_by_hand_is_not_listed(self):
        n = self.number()
        n.status = PhoneNumber.Status.RELEASED
        n.released_at = timezone.now()
        n.save()
        self.assertEqual(self.notice()['count'], 0)

    def test_partners_get_nothing(self):
        retire(self.number(), 'used')
        pub = User.objects.create(username='p', email='p@x.io', organization=self.org, role='publisher')
        self.assertEqual(self.notice(pub)['count'], 0)

    def test_other_workspaces_never_show(self):
        other_org = Organization.objects.create(name='Other', slug='other')
        stranger = User.objects.create(username='s', email='s@x.io', organization=other_org, role='admin')
        retire(self.number(), 'used')
        self.assertEqual(self.notice(stranger)['count'], 0)
