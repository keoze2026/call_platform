"""CH-565: the call panel Sir drew - every TFN rung, timing, pay rule, ids.

Run:  python manage.py test analytics.test_call_panel
"""
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from publishers.models import Publisher
from routing.models import CallLog, RoutingRule, RuleDestination

Q15, CRM = '+18669496771', '+18445442126'


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class CallPanelTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.user = User.objects.create(username='a', email='a@x.io', organization=self.org,
                                        role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}')
        self.camp = Campaign.objects.create(organization=self.org, name='C-07')
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO 1')
        self.q15 = Buyer.objects.create(organization=self.org, name='Q15')
        self.crm = Buyer.objects.create(organization=self.org, name='CRM')
        Destination.objects.create(organization=self.org, buyer=self.q15, name='Q15|6771|', tfn=Q15)
        Destination.objects.create(organization=self.org, buyer=self.crm, name='CRM|2126|', tfn=CRM)
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.camp, name='R',
                                          rule_type='priority', priority=1)
        self.line = RuleDestination.objects.create(rule=rule, buyer=self.crm, destination=CRM,
                                                   convert_on='connected')
        t0 = timezone.now() - timedelta(minutes=5)
        self.t0 = t0
        self.call = CallLog.objects.create(
            organization=self.org, campaign=self.camp, publisher=self.pub, buyer=self.crm,
            rule_destination=self.line, caller_number='+13522837294', called_number='+18552026099',
            destination_number=CRM, twilio_call_sid='1793803172.39924', status='completed',
            duration=27, revenue=1, answered_at=t0 + timedelta(seconds=20),
            ended_at=t0 + timedelta(seconds=47), ring_seconds=20)
        CallLog.objects.filter(pk=self.call.pk).update(created_at=t0)
        # Q15 rang 14 s and missed; the miss row is written when reported and
        # backdated by its ring time - here 0, as Server A sends today.
        miss = CallLog.objects.create(
            organization=self.org, campaign=self.camp, buyer=self.q15, caller_number='+13522837294',
            called_number='+18552026099', destination_number=Q15,
            twilio_call_sid='1793803172.39924#try1', status='no_answer', block_reason='No Answer',
            ring_seconds=0, retry_hidden=True, retry_root=self.call, ended_at=t0 + timedelta(seconds=14))
        CallLog.objects.filter(pk=miss.pk).update(created_at=t0 + timedelta(seconds=14))

    def panel(self):
        r = self.api.get(f'/api/analytics/calls/{self.call.id}/detail')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['panel']

    def test_every_tfn_rung_with_result_and_ring_time(self):
        legs = self.panel()['legs']
        self.assertEqual([(l['name'], l['result'], l['ring_seconds'], l['talk_seconds']) for l in legs],
                         [('Q15|6771|', 'no_answer', 14, 0), ('CRM|2126|', 'answered', 6, 27)])

    def test_timing_ids_and_pay_rule(self):
        p = self.panel()
        self.assertEqual((p['answered_in'], p['talk_seconds'], p['total_seconds']), (20, 27, 47))
        self.assertEqual(p['session'], 'S-39924')
        self.assertRegex(p['display_id'], r'^AVX-[0-9A-F]{4}-[0-9A-F]{4}$')
        self.assertEqual(p['destination_name'], 'CRM|2126|')
        self.assertEqual(p['convert_rule'], {'on': 'connected', 'seconds': 0})
        self.assertEqual(p['sender_tries'], 1)
        self.assertFalse(p['repeat'])

    def test_a_missed_call_has_no_answer_time(self):
        CallLog.objects.filter(pk=self.call.pk).update(status='no_answer', answered_at=None, duration=0,
                                                       block_reason='Not Connected')
        p = self.panel()
        self.assertIsNone(p['answered_in'])
        self.assertEqual(p['legs'][-1]['result'], 'no_answer')
        self.assertEqual(p['legs'][-1]['reason'], 'Not Connected')
        self.assertEqual(p['legs'][-1]['ring_seconds'], 33)

    def test_nothing_from_the_ai_audit(self):
        import json
        CallLog.objects.filter(pk=self.call.pk).update(ai_label='dead_caller', ai_summary='secret summary')
        p = self.panel()
        self.assertFalse([k for k in p if k.startswith('ai')])
        self.assertNotIn('dead_caller', json.dumps(p))
        self.assertNotIn('secret summary', json.dumps(p))


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class CallPanelPartnerTests(CallPanelTests):
    """CH-566: partner logins see in the panel only what their Call Log gives them."""

    def as_login(self, role, **link):
        u = User.objects.create(username=f'{role}-login', email=f'{role}@x.io', organization=self.org,
                                role=role, **link)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')

    def detail(self):
        r = self.api.get(f'/api/analytics/calls/{self.call.id}/detail')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_a_buyer_never_sees_ttc_other_buyers_or_our_side(self):
        self.as_login('buyer', buyer=self.crm)
        d = self.detail()
        p = d['panel']
        self.assertEqual((p['answered_in'], p['wait_seconds'], p['total_seconds'], p['answered_at'], p['ended_at']),
                         (None, None, None, None, None))
        self.assertEqual([e['at'] is None for e in d['timeline']], [e['event'] != 'call_received' for e in d['timeline']])
        # Only CRM's own line; Q15's miss (another buyer) is not there.
        self.assertEqual([(l['name'], l['ring_seconds']) for l in p['legs']], [('CRM|2126|', None)])
        self.assertEqual(d['routing_trace'], {})
        self.assertIsNone(d['routing']['campaign_name'])
        self.assertIsNone(d['routing']['publisher_name'])
        self.assertIsNone(d['routing']['called_number'])
        self.assertIsNone(d['financials']['payout'])

    def test_a_publisher_sees_no_revenue(self):
        self.as_login('publisher', publisher=self.pub)
        d = self.detail()
        self.assertIsNone(d['financials']['revenue'])
        self.assertEqual(d['panel']['answered_in'], 20)

    def test_no_internal_or_server_names_in_reasons(self):
        for raw, shown in (('asterisk_gone', 'Not Connected'), ('Routed by Server A', None)):
            CallLog.objects.filter(pk=self.call.pk).update(status='no_answer', answered_at=None, duration=0,
                                                           block_reason=raw)
            d = self.detail()
            self.assertEqual(d['panel']['legs'][-1]['reason'], shown)
            self.assertEqual(d['routing']['block_reason'], shown)
            self.assertNotIn('Server A', str(d))
