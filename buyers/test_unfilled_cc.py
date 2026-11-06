"""CH-626 (Sir, 6 Nov): "Unfilled CC means free CC. If someone has a 10 cap
with 3 CC and the 10 cap is over, the TFN is active but more calls cannot go
due to the cap, so it should not be counted as unfilled. Unfilled means we
can still fill them and we have cap for that destination."

Run:  python manage.py test buyers.test_unfilled_cc
"""
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer, BuyerCap
from routing.models import CallLog


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class UnfilledCcTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin')
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        self.n = 0

    def buyer(self, name, status='active', daily=0):
        b = Buyer.objects.create(organization=self.org, name=name, status=status)
        cap, _ = BuyerCap.objects.get_or_create(buyer=b)
        cap.max_calls_daily = daily
        cap.save()
        return b

    def line(self, buyer, tfn, cc, enabled=True, **caps):
        return Destination.objects.create(organization=self.org, buyer=buyer, name=f'{buyer.name}|{tfn[-4:]}',
                                          tfn=tfn, concurrency_cap=cc, enabled=enabled, **caps)

    def call(self, line, live=False):
        self.n += 1
        return CallLog.objects.create(
            organization=self.org, buyer=line.buyer, caller_number=f'+1555000{self.n:04d}',
            called_number='+18005550100', twilio_call_sid=f'CAUCC{self.n}', destination_number=line.tfn,
            status='in_progress' if live else 'completed', duration=0 if live else 60)

    def stats(self):
        r = self.api.get('/api/destinations/stats/')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_a_line_whose_cap_is_full_has_no_unfilled_cc(self):
        q35 = self.buyer('Q35')
        full = self.line(q35, '+18444896670', cc=7, daily_cap=25)     # Sir's example: cap over
        for _ in range(25):
            self.call(full)
        s = self.stats()
        self.assertEqual((s['vacant_cc'], s['capped_lines']), (0, 1))
        self.assertEqual(s['total_cc'], 7)                             # Total CC unchanged

    def test_free_cc_counts_where_a_call_can_still_land(self):
        hoz = self.buyer('HOZ')
        open_line = self.line(hoz, '+18334973067', cc=9, daily_cap=200)
        for _ in range(3):
            self.call(open_line)                                       # 3 today, under the cap
        for _ in range(2):
            self.call(open_line, live=True)                            # 2 on the line now
        self.line(hoz, '+18885550001', cc=5, enabled=False)            # switched off: nothing
        s = self.stats()
        self.assertEqual((s['vacant_cc'], s['vacant_lines'], s['capped_lines']), (9 - 2, 1, 0))

    def test_paused_buyer_and_full_buyer_cap_count_nothing(self):
        paused = self.buyer('PAU', status='paused')
        self.line(paused, '+18885550002', cc=4)
        capped = self.buyer('CAP', daily=2)
        l2 = self.line(capped, '+18885550003', cc=3)
        self.call(l2)
        self.call(l2)                                                  # buyer's daily cap 2/2
        ok = self.buyer('OK')
        self.line(ok, '+18885550004', cc=2)
        s = self.stats()
        self.assertEqual((s['vacant_cc'], s['vacant_lines'], s['capped_lines']), (2, 1, 2))

    def test_a_line_over_its_cc_does_not_hide_another_lines_free_cc(self):
        b = self.buyer('B1')
        busy = self.line(b, '+18885550005', cc=1)
        for _ in range(3):
            self.call(busy, live=True)                                 # 3 live on CC 1
        self.line(b, '+18885550006', cc=4)
        self.assertEqual(self.stats()['vacant_cc'], 4)                 # was 5 - 3 = 2
