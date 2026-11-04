"""CH-538: calls reach Reports at once; one live rule everywhere; the cache is
cleared by call events and the portal is told over the WebSocket.

Run:  python manage.py test routing.test_realtime_reports
"""
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing import call_events
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
LINE = '+18889012612'


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
)
class RealtimeReportsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(username='owner', email='o@x.io', organization=self.org,
                                         role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-15',
                                                revenue_amount=Decimal('1.00'), payout_amount=Decimal('0'),
                                                min_call_duration=0, duplicate_handling='normal')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        self.buyer = Buyer.objects.create(organization=self.org, name='CDM')
        Destination.objects.create(organization=self.org, buyer=self.buyer, name='CDM|2612', tfn=LINE, enabled=True)
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        RuleDestination.objects.create(rule=rule, destination=LINE, buyer=self.buyer, priority=1)
        # No Celery worker in these tests: whatever still waits for one never happens.
        for p in (mock.patch.object(RoutingEngine, 'check_balance', return_value=True),
                  mock.patch('celery.app.task.Task.apply_async')):
            p.start()
            self.addCleanup(p.stop)
        self.n = 0

    def post(self, path, body):
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'), \
                mock.patch('tasks.retire_used_number.delay'), self.captureOnCommitCallbacks(execute=True):
            return Client().post(path, data=json.dumps(body), content_type='application/json',
                                 HTTP_X_ASTERISK_SECRET=SECRET)

    def route(self):
        self.n += 1
        self.post('/api/twilio/asterisk/route/', {'caller_number': f'+1929213{self.n:04d}', 'called_number': TFN,
                                                  'asterisk_call_id': f'sid-{self.n}'})
        return CallLog.objects.get(twilio_call_sid=f'sid-{self.n}')

    def end(self, row, answered=True, duration=65):
        self.post('/api/twilio/asterisk/call-ended/', {
            'call_log_id': str(row.id), 'duration': duration if answered else 0, 'answered': answered,
            'dialed_seconds': 9, 'dial_status': 'ANSWER' if answered else 'NOANSWER'})
        row.refresh_from_db()
        return row

    def today(self, path, extra=''):
        day = timezone.now().date().isoformat()
        r = self.api.get(f'{path}?date_from={day}&date_to={day}&timezone=UTC{extra}')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def make(self, status, ended=False, hours_ago=0):
        self.n += 1
        row = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyer,
                                     caller_number=f'+1555{self.n:07d}', called_number=TFN,
                                     destination_number=LINE, twilio_call_sid=f'm-{self.n}', status=status,
                                     ended_at=timezone.now() if ended else None)
        if hours_ago:
            CallLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=hours_ago))
        return row

    # ── the call is in Reports the moment it ends ───────────────────────────

    def test_an_ended_call_is_in_reports_at_once_without_the_worker(self):
        row = self.route()
        self.assertEqual(len(self.today('/api/analytics/calls')['items']), 1)    # live
        row = self.end(row)
        self.assertEqual(row.status, 'completed')
        self.assertTrue(CallRecord.objects.filter(pk=row.pk).exists())            # copied in the request
        items = self.today('/api/analytics/calls')['items']
        self.assertEqual([i['id'] for i in items], [str(row.id)])                 # never missing
        self.assertEqual(self.today('/api/analytics/dashboard')['total_calls'], 1)

    def test_a_missed_call_too(self):
        row = self.end(self.route(), answered=False)
        self.assertEqual(row.status, 'no_answer')
        self.assertEqual(len(self.today('/api/analytics/calls')['items']), 1)

    # ── call events clear the cache and are pushed ──────────────────────────

    def test_every_event_moves_the_version_and_pushes_no_details(self):
        layer = get_channel_layer()
        channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)(f'reports_org_{self.org.id}', channel)
        before = call_events.report_version(self.org.id)
        row = self.route()
        msg = async_to_sync(layer.receive)(channel)
        self.assertEqual(msg, {'type': 'report_changed', 'data': {'event': 'call.created'}})
        self.assertNotEqual(call_events.report_version(self.org.id), before)
        self.end(row)
        events = []
        while True:
            try:
                events.append(async_to_sync(layer.receive)(channel)['data']['event'])
            except Exception:
                break
            if 'call.ended' in events:
                break
        self.assertIn('call.ended', events)

    def test_header_shows_a_new_call_on_the_next_request(self):
        self.assertEqual(self.today('/api/analytics/dashboard')['total_calls'], 0)
        with self.captureOnCommitCallbacks(execute=True):
            self.make('ringing')
        # The header's own cache (a few seconds) does not hide it.
        data = self.today('/api/analytics/dashboard')
        self.assertEqual((data['total_calls'], data['live_calls']), (1, 1))

    def test_the_socket_sends_only_the_event_name(self):
        from routing.consumers import LiveCallConsumer
        sent = []
        consumer = LiveCallConsumer()

        async def send(text_data=None, **kw):
            sent.append(json.loads(text_data))
        consumer.send = send
        async_to_sync(consumer.report_changed)({'type': 'report_changed',
                                                'data': {'event': 'call.ended', 'caller': '+1555'}})
        self.assertEqual(sent, [{'type': 'report.changed', 'data': {'event': 'call.ended'}}])

    # ── one live rule ────────────────────────────────────────────────────────

    def test_header_live_monitor_reports_and_campaign_count_live_the_same(self):
        self.make('ringing')
        self.make('in_progress')
        self.make('completed', ended=True)
        self.make('in_progress', hours_ago=5)              # stuck row: not live anywhere
        header = self.api.get('/api/analytics/live/summary').json()['in_flight']
        monitor = len(self.api.get('/api/routing/calls/live').json())
        monitor_fallback = len(self.api.get('/api/analytics/live').json())
        reports = self.today('/api/analytics/dashboard')['live_calls']
        campaign = sum(self.api.get('/api/routing/calls/live-counts').json()['destinations'].values())
        self.assertEqual((header, monitor, monitor_fallback, reports, campaign), (2, 2, 2, 2, 2))
