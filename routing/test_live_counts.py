"""CH-211: live calls per tracking number and per destination, for the LIVE
column on the campaign page.

Run:  python manage.py test routing.test_live_counts
"""
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from routing.models import CallLog


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class LiveCountsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(username='owner', email='owner@x.io', organization=self.org,
                                         role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.camp = Campaign.objects.create(organization=self.org, name='PDSO')
        self.n = 0

    def call(self, tfn, dest, status, ended=False, age_hours=0, org=None):
        self.n += 1
        row = CallLog.objects.create(
            organization=org or self.org, campaign=self.camp, caller_number='+15551234567',
            called_number=tfn, destination_number=dest, twilio_call_sid=f'sid-{self.n}',
            status=status, ended_at=timezone.now() if ended else None)
        if age_hours:
            CallLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=age_hours))
        return row

    def counts(self):
        r = self.api.get('/api/routing/calls/live-counts')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_ringing_and_connected_calls_are_counted(self):
        self.call('+18888910573', '+18667404516', CallLog.Status.IN_PROGRESS)
        self.call('+18888910573', '+18667404516', CallLog.Status.RINGING)
        self.call('+18888910563', '18667404516', CallLog.Status.IN_PROGRESS)
        out = self.counts()
        self.assertEqual(out['tracking'], {'8888910573': 2, '8888910563': 1})
        self.assertEqual(out['destinations'], {'8667404516': 3})

    def test_finished_stale_and_other_accounts_calls_are_not_live(self):
        other = Organization.objects.create(name='Other', slug='other')
        self.call('+18888910573', '+18667404516', CallLog.Status.COMPLETED, ended=True)
        self.call('+18888910573', '+18667404516', CallLog.Status.NO_ANSWER, ended=True)
        self.call('+18888910573', '+18667404516', CallLog.Status.IN_PROGRESS, age_hours=5)
        self.call('+18888910573', '+18667404516', CallLog.Status.IN_PROGRESS, org=other)
        self.assertEqual(self.counts(), {'tracking': {}, 'destinations': {}})

    def test_a_call_not_yet_sent_counts_only_on_its_tfn(self):
        self.call('+18888910573', '', CallLog.Status.RINGING)
        out = self.counts()
        self.assertEqual(out['tracking'], {'8888910573': 1})
        self.assertEqual(out['destinations'], {})
