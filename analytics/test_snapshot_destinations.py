"""CH-404: the dashboard snapshot lists every line that had calls in the day.

Sir, 30 Oct (buyer access): the Destinations / Buyers card was empty for a
buyer with 80 calls on the chart above it - its TFN had been switched off after
its cap, and the snapshot listed switched-on lines only.

Run:  python manage.py test analytics.test_snapshot_destinations
"""
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer
from routing.models import CallLog

ON, OFF_USED, OFF_IDLE = '+18883413566', '+18554707586', '+18663990119'


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class SnapshotDestinationsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='m', email='m@x.io', organization=self.org,
                                         role='admin', is_superuser=True)
        self.mtg = Buyer.objects.create(organization=self.org, name='MTG')
        self.r48 = Buyer.objects.create(organization=self.org, name='R48')
        self.on = Destination.objects.create(organization=self.org, buyer=self.mtg, name='MTG', tfn=ON, enabled=True)
        # An older switched-off copy of the same line: history, not a second line.
        Destination.objects.create(organization=self.org, buyer=self.r48, name='R48 old', tfn=OFF_USED,
                                   enabled=False)
        self.off_used = Destination.objects.create(organization=self.org, buyer=self.r48, name='R48|7586',
                                                   tfn=OFF_USED, enabled=False, daily_cap=12)
        Destination.objects.create(organization=self.org, buyer=self.r48, name='R48|0119', tfn=OFF_IDLE,
                                   enabled=False)
        for i in range(3):
            CallLog.objects.create(organization=self.org, buyer=self.r48, caller_number=f'+1555000000{i}',
                                   called_number='+18888910343', twilio_call_sid=f's-{i}',
                                   destination_number=OFF_USED, status='completed')
        # A refusal stamped on the idle line was never sent to it.
        CallLog.objects.create(organization=self.org, buyer=self.r48, caller_number='+15550009999',
                               called_number='+18888910343', twilio_call_sid='refused',
                               destination_number=OFF_IDLE, status='no_answer', block_reason='No Destination')

    def snapshot(self, user):
        api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')
        r = api.get('/api/analytics/snapshot?timezone=America/New_York')
        self.assertEqual(r.status_code, 200, r.content)
        return {d['name']: d for d in r.json()['destinations']}

    def test_a_switched_off_line_with_calls_today_is_listed_once(self):
        rows = self.snapshot(self.admin)
        self.assertEqual(set(rows), {'MTG', 'R48|7586'})
        self.assertEqual(rows['R48|7586']['daily_calls'], 3)
        self.assertFalse(rows['R48|7586']['enabled'])

    def test_a_buyer_login_sees_its_own_lines_with_calls(self):
        user = User.objects.create(username='r48', email='r48@x.io', organization=self.org, role='buyer')
        if hasattr(user, 'buyer_id'):
            user.buyer = self.r48
            user.save()
        else:
            self.skipTest('no buyer link on User in this schema')
        rows = self.snapshot(user)
        self.assertEqual(set(rows), {'R48|7586'})
