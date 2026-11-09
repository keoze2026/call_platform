"""CH-648 (Sir, 9 Nov: "There is no way any client can see anyone else report or
tfn. No way at all - fix our setting accordingly").

A caller rang A49's line, which did not answer, and the same call moved on to
BOP's line (CH-514). The report copy (CallRecord) kept "A49", so A49's login saw
BOP's call - with BOP's number while it was live. A buyer or publisher login
now sees a report row only when the call itself (CallLog) is theirs too; a row
with no call behind it keeps its own buyer, as before. Staff see everything.

Run:  python manage.py test accounts.test_call_copy_guard
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from accounts.permissions import scope_queryset
from analytics.models import CallRecord
from buyers.models import Buyer
from campaigns.models import Campaign
from publishers.models import Publisher
from routing.models import CallLog
from routing.signals import mirror_call_log

ET = ZoneInfo('America/New_York')


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class CallCopyGuardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='L-10', status='active')
        self.a49 = Buyer.objects.create(organization=self.org, name='A49')
        self.bop = Buyer.objects.create(organization=self.org, name='BOP')
        self.pub1 = Publisher.objects.create(organization=self.org, name='DS-01')
        self.pub2 = Publisher.objects.create(organization=self.org, name='DS-02')
        self.n = 0

    def user(self, role, **kw):
        self.n += 1
        return User.objects.create(username=f'u{self.n}', email=f'u{self.n}@x.io',
                                   organization=self.org, role=role, **kw)

    def call(self, buyer, publisher, number):
        self.n += 1
        c = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=buyer,
                                   publisher=publisher, caller_number='+17169133430',
                                   called_number='+18005550101', destination_number=number,
                                   twilio_call_sid=f's{self.n}', status='no_answer',
                                   ended_at=timezone.now() - timedelta(minutes=1))
        mirror_call_log(c.id)
        return c

    def seen(self, user):
        day = timezone.now().astimezone(ET).date().isoformat()
        r = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}').get(
            f'/api/analytics/calls?date_from={day}&date_to={day}&timezone=America/New_York')
        self.assertEqual(r.status_code, 200, r.content)
        return {i['id'] for i in r.json()['items']}

    def test_a_copy_left_behind_is_not_shown_to_the_buyer_it_names(self):
        # The call is BOP's; its report copy still says A49 (9 Nov, 11:01 AM).
        c = self.call(self.bop, self.pub1, '+18889022495')
        CallRecord.objects.filter(id=c.id).update(buyer_id=self.a49.id, buyer_name='A49',
                                                   destination_number='+18889022495')
        a49 = self.user('buyer', buyer=self.a49)
        self.assertNotIn(str(c.id), self.seen(a49))
        self.assertFalse(scope_queryset(a49, CallRecord.objects.all(), 'buyer_id', 'publisher_id').exists())
        # Staff still see the row.
        self.assertIn(str(c.id), self.seen(self.user('admin', is_superuser=True)))

    def test_a_buyers_own_call_is_shown(self):
        c = self.call(self.a49, self.pub1, '+18443132203')
        self.assertIn(str(c.id), self.seen(self.user('buyer', buyer=self.a49)))
        self.assertNotIn(str(c.id), self.seen(self.user('buyer', buyer=self.bop)))

    def test_a_row_with_no_call_behind_it_keeps_its_buyer(self):
        r = CallRecord.objects.create(organization=self.org, campaign=self.campaign, campaign_name='L-10',
                                      buyer_id=self.a49.id, buyer_name='A49',
                                      caller_number='+15550000009', called_number='+18005550101',
                                      destination_number='+18443132203', status='completed',
                                      started_at=timezone.now())
        self.assertIn(str(r.id), self.seen(self.user('buyer', buyer=self.a49)))

    def test_publisher_the_same(self):
        c = self.call(self.a49, self.pub1, '+18443132203')
        CallRecord.objects.filter(id=c.id).update(publisher_id=self.pub2.id, publisher_name='DS-02')
        pub2 = self.user('publisher', publisher=self.pub2)
        self.assertFalse(scope_queryset(pub2, CallRecord.objects.all(), 'buyer_id', 'publisher_id').exists())
        self.assertNotIn(str(c.id), self.seen(pub2))

    def test_other_lists_are_unchanged(self):
        # The guard is for report rows only: the call list itself is scoped as before.
        c = self.call(self.bop, self.pub1, '+18889022495')
        bop = self.user('buyer', buyer=self.bop)
        self.assertEqual(list(scope_queryset(bop, CallLog.objects.all()).values_list('id', flat=True)), [c.id])
