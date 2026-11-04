"""CH-527: a caller refused again and again while no line is on shows once.

Sir, 4 Nov: "When destinations are not active ... on our portal it should only
show 1-2 times max, but as soon as destination on it keep trying to all active
destination with space cc."

Run:  python manage.py test routing.test_refused_repeats
"""
import io
import json
from datetime import timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from analytics.models import CallRecord
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN1, TFN2 = '+18443754175', '+18332013844'
Q11, Q04 = '+18334094605', '+18443125984'
CALLER = '+13868645240'


@override_settings(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, FAILOVER_RING_SECONDS=15,
                   REFUSED_REPEAT_MINUTES=30,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RefusedRepeatTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-10', status='active')
        for n in (TFN1, TFN2):
            PhoneNumber.objects.create(organization=self.org, number=n, vendor='Other',
                                       campaign=self.campaign, status='active')
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        self.lines = {}
        for i, (name, n) in enumerate((('Q11', Q11), ('Q04', Q04)), start=1):
            b = Buyer.objects.create(organization=self.org, name=name)
            self.lines[name] = Destination.objects.create(organization=self.org, buyer=b, name=name,
                                                          tfn=n, enabled=False)
            RuleDestination.objects.create(rule=rule, destination=n, buyer=b, priority=i)
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def dial(self, called, caller=CALLER):
        self.n += 1
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'):
            r = Client().post('/api/twilio/asterisk/route/', data=json.dumps({
                'caller_number': caller, 'called_number': called, 'asterisk_call_id': f'sid-{self.n}'}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def age_all(self, minutes):
        # Each earlier call happened `minutes` before the next one.
        for c in CallLog.objects.all():
            CallLog.objects.filter(pk=c.pk).update(created_at=c.created_at - timedelta(minutes=minutes),
                                                   ended_at=(c.ended_at or c.created_at) - timedelta(minutes=minutes))

    def shown(self):
        return list(CallLog.objects.filter(retry_hidden=False).order_by('created_at'))

    def test_refused_again_and_again_shows_once(self):
        for called in (TFN1, TFN2, TFN1, TFN2, TFN1):        # minutes apart, two TFNs
            self.dial(called)
            self.age_all(2)
        shown = self.shown()
        self.assertEqual(len(shown), 1)
        self.assertEqual((shown[0].block_reason, shown[0].destination_number), ('No Destination', ''))
        self.assertEqual(CallLog.objects.count(), 5)                         # every try kept
        self.assertEqual(CallLog.objects.filter(retry_hidden=True).count(), 4)
        self.assertEqual(list(CallRecord.objects.values_list('id', flat=True)), [shown[0].id])

    def test_as_soon_as_a_line_is_on_the_call_goes_through(self):
        self.dial(TFN1)
        self.age_all(2)
        self.dial(TFN2)
        self.age_all(2)
        Destination.objects.filter(pk=self.lines['Q04'].pk).update(enabled=True)
        cache.clear()
        out = self.dial(TFN1)
        self.assertEqual(out['buyer_number'], Q04)
        rows = self.shown()
        self.assertEqual(len(rows), 2)                                       # 1 refused + 1 sent
        self.assertEqual(rows[-1].destination_number, Q04)

    def test_another_caller_or_a_long_gap_is_its_own_row(self):
        self.dial(TFN1)
        self.dial(TFN1, caller='+15550001111')
        self.age_all(45)                                                     # past the 30 minutes
        self.dial(TFN1)
        self.assertEqual(len(self.shown()), 3)

    def test_past_day_command(self):
        for called in (TFN1, TFN2, TFN1):
            self.dial(called)
            self.age_all(2)
        CallLog.objects.update(retry_hidden=False, retry_root=None)          # as before CH-527
        day = timezone.now().astimezone(ZoneInfo('America/New_York')).date().isoformat()
        out = io.StringIO()
        call_command('fold_refused_repeats', '--date', day, stdout=out)
        self.assertIn('3 "No Destination" rows shown; 2 are the same caller refused again -> 1 rows after',
                      out.getvalue())
        self.assertEqual(len(self.shown()), 3)                               # dry run
        call_command('fold_refused_repeats', '--date', day, '--apply', stdout=io.StringIO())
        self.assertEqual(len(self.shown()), 1)
        self.assertEqual(CallLog.objects.count(), 3)
