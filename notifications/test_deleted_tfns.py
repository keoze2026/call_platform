"""CH-179 / CH-182: the "TFNs deleted" notice stays until 12 PM Eastern.

Run:  python manage.py test notifications.test_deleted_tfns
"""
import json
from contextlib import contextmanager
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from phone_numbers.lifecycle import retire
from phone_numbers.models import Carrier, PhoneNumber

URL = '/api/notifications/deleted-tfns'
ET = ZoneInfo('America/New_York')


def et(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=ET)


@contextmanager
def clock(when):
    with mock.patch('django.utils.timezone.now', return_value=when):
        yield


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

    def retire_at(self, when, why='used'):
        self.n += 1
        n = PhoneNumber.objects.create(
            organization=self.org, number=f'+1833000{self.n:04d}', carrier=self.kmq,
            twilio_sid=f'TFN-{self.n}', status=PhoneNumber.Status.ACTIVE,
        )
        with clock(when):
            retire(n, why)
        return n

    def client_for(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def notice(self, when, user=None):
        with clock(when):
            return self.client_for(user or self.admin).get(URL).json()

    def seen(self, when, user=None, **body):
        with clock(when):
            return self.client_for(user or self.admin).post(
                URL + '/seen', data=json.dumps(body), content_type='application/json').json()

    # ---- the 12 PM Eastern rule
    def test_overnight_retirement_stays_all_morning(self):
        self.retire_at(et(21, 1, 30), '12 answered calls on 2026-10-20, over the limit of 10, nightly 1:30 AM rule')
        data = self.notice(et(21, 11, 59))
        self.assertEqual(data['count'], 1)
        self.assertIn('12 answered calls on 2026-10-20', data['numbers'][0]['reason'])
        self.assertEqual(data['visible_until'], et(21, 12).isoformat())

    def test_it_drops_off_at_noon_eastern(self):
        self.retire_at(et(21, 1, 30))
        self.assertEqual(self.notice(et(21, 12, 0))['count'], 0)
        self.assertEqual(self.notice(et(21, 15))['count'], 0)

    def test_retired_after_noon_stays_until_noon_next_day(self):
        self.retire_at(et(21, 13))
        self.assertEqual(self.notice(et(21, 23))['count'], 1)
        self.assertEqual(self.notice(et(22, 11, 59))['count'], 1)
        self.assertEqual(self.notice(et(22, 12, 1))['count'], 0)

    # ---- Seen acknowledges, it does not hide
    def test_seen_keeps_the_list_and_clears_the_count(self):
        self.retire_at(et(21, 1, 30))
        self.assertEqual(self.notice(et(21, 8))['unseen_count'], 1)
        after = self.seen(et(21, 8, 5))
        self.assertEqual((after['count'], after['unseen_count']), (1, 0))
        self.assertTrue(after['numbers'][0]['seen'])
        again = self.notice(et(21, 11))
        self.assertEqual((again['count'], again['unseen_count']), (1, 0))

    def test_a_new_retirement_after_seen_is_unseen(self):
        self.retire_at(et(21, 1, 30))
        self.seen(et(21, 8))
        self.retire_at(et(21, 9), 'expired')
        data = self.notice(et(21, 9, 1))
        self.assertEqual((data['count'], data['unseen_count']), (2, 1))
        self.assertEqual(data['numbers'][0]['reason'], 'Time limit reached')
        self.assertFalse(data['numbers'][0]['seen'])

    def test_seen_up_to_keeps_a_number_retired_after_the_notice_was_shown(self):
        self.retire_at(et(21, 1, 30))
        shown = self.notice(et(21, 8))['newest_at']
        self.retire_at(et(21, 8, 1))
        self.seen(et(21, 8, 2), up_to=shown)
        self.assertEqual(self.notice(et(21, 8, 3))['unseen_count'], 1)

    def test_seen_is_per_user(self):
        other = User.objects.create(username='b', email='b@x.io', organization=self.org, role='admin')
        self.retire_at(et(21, 1, 30))
        self.seen(et(21, 8))
        self.assertEqual(self.notice(et(21, 8, 1), other)['unseen_count'], 1)

    # ---- who sees what
    def test_a_number_deleted_by_hand_is_not_listed(self):
        n = self.retire_at(et(21, 1, 30))
        PhoneNumber.objects.filter(pk=n.pk).update(retired_reason='')
        self.assertEqual(self.notice(et(21, 8))['count'], 0)

    def test_partners_get_nothing(self):
        self.retire_at(et(21, 1, 30))
        pub = User.objects.create(username='p', email='p@x.io', organization=self.org, role='publisher')
        self.assertEqual(self.notice(et(21, 8), pub)['count'], 0)

    def test_other_workspaces_never_show(self):
        other_org = Organization.objects.create(name='Other', slug='other')
        stranger = User.objects.create(username='s', email='s@x.io', organization=other_org, role='admin')
        self.retire_at(et(21, 1, 30))
        self.assertEqual(self.notice(et(21, 8), stranger)['count'], 0)
