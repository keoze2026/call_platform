"""CH-284: Call Summary shows Cost on every tab, not only Campaign.

Sir, 26 Oct: "Cost is only visible under campaign, rest all column empty".
The Publisher summary computed the cost and never sent it, and the call log
carried no per-call cost, so the tabs the page totals from the calls
themselves (Dialed, Destination, Date, ...) had nothing to add up.

Run:  python manage.py test analytics.test_summary_cost
"""
import uuid
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from billing.models import BillingAccount


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class SummaryCostTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        BillingAccount.objects.create(
            organization=self.org, status=BillingAccount.Status.ACTIVE, balance=Decimal('0'),
            per_minute_rate=Decimal('0.45'), markup_percent=0, recording_fee_per_minute=0,
            voip_shield_fee_per_call=0, rejected_call_fee=Decimal('0'))
        owner = User.objects.create(username='o', email='o@x.io', organization=self.org,
                                    role='admin', is_superuser=True)
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(owner)}')
        self.day = timezone.now().date().isoformat()
        self.pub = uuid.uuid4()

    def record(self, **kw):
        fields = dict(organization=self.org, caller_number='+15551234567',
                      called_number='+18005550100', status='completed',
                      started_at=timezone.now(), publisher_id=self.pub, publisher_name='Crug')
        fields.update(kw)
        return CallRecord.objects.create(**fields)

    def get(self, path):
        r = self.client.get(f'/api/analytics/{path}?date_from={self.day}&date_to={self.day}&timezone=UTC&limit=100')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_every_call_in_the_log_carries_its_cost(self):
        self.record(duration_seconds=75, platform_cost=Decimal('0.90'))   # charged
        self.record(duration_seconds=61)                                   # never charged: 2 min estimate
        self.record(status='no_answer', duration_seconds=0)                # missed: nothing
        costs = sorted(Decimal(str(c['cost'])) for c in self.get('calls')['items'])
        self.assertEqual(costs, [Decimal('0'), Decimal('0.90'), Decimal('0.90')])

    def test_publisher_tab_has_the_cost_campaign_has(self):
        self.record(duration_seconds=75, platform_cost=Decimal('0.90'))
        self.record(duration_seconds=30, platform_cost=Decimal('0.45'))
        rows = self.get('publishers')
        self.assertEqual(len(rows), 1)
        self.assertEqual(Decimal(str(rows[0]['total_cost'])), Decimal('1.35'))
        self.assertEqual(rows[0]['billable_minutes'], 3)

    def test_the_calls_add_up_to_the_summary(self):
        self.record(duration_seconds=75, platform_cost=Decimal('0.90'))
        self.record(duration_seconds=30, platform_cost=Decimal('0.45'))
        self.record(duration_seconds=200, platform_cost=Decimal('1.80'))
        per_call = sum(Decimal(str(c['cost'])) for c in self.get('calls')['items'])
        summary = Decimal(str(self.get('publishers')[0]['total_cost']))
        self.assertEqual(per_call, summary)

    def test_a_publisher_login_is_not_told_the_cost(self):
        self.record(duration_seconds=75, platform_cost=Decimal('0.90'))
        from analytics.services import mask_partner_money
        partner = User(organization=self.org, role='publisher')
        masked = mask_partner_money(partner, [{'cost': Decimal('0.90'), 'total_cost': Decimal('0.90')}])
        self.assertIsNone(masked[0]['cost'])
        self.assertIsNone(masked[0]['total_cost'])
