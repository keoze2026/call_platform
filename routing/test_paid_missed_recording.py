"""CH-535: paid calls nobody answered keep their recording; Server A records
from the moment the buyer is dialled.

Run:  python manage.py test routing.test_paid_missed_recording
"""
import importlib.util
import json
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
LINE = '+18667404516'


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False,
    FRONTEND_URL='https://app.example.com', PAID_MISSED_RECORDING=True,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class PaidMissedRecordingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-10',
                                                revenue_amount=Decimal('1.00'), payout_amount=Decimal('1.00'),
                                                min_call_duration=0, duplicate_handling='normal')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        buyer = Buyer.objects.create(organization=self.org, name='HOZ')
        Destination.objects.create(organization=self.org, buyer=buyer, name='HOZ|4516', tfn=LINE, enabled=True)
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        self.row = RuleDestination.objects.create(rule=rule, destination=LINE, buyer=buyer, priority=1)
        for p in (mock.patch.object(RoutingEngine, 'check_balance', return_value=True),
                  mock.patch('celery.app.task.Task.apply_async')):
            p.start()
            self.addCleanup(p.stop)
        self.n = 0

    def call(self, answered, duration, convert_on, dial_status='NOANSWER'):
        RuleDestination.objects.filter(pk=self.row.pk).update(convert_on=convert_on, revenue=Decimal('1.00'))
        self.n += 1
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'), \
                mock.patch('tasks.retire_used_number.delay'), self.captureOnCommitCallbacks(execute=True):
            c = Client()
            c.post('/api/twilio/asterisk/route/', data=json.dumps(
                {'caller_number': f'+1555000{self.n:04d}', 'called_number': TFN,
                 'asterisk_call_id': f'sid-{self.n}'}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
            row = CallLog.objects.get(twilio_call_sid=f'sid-{self.n}')
            c.post('/api/twilio/asterisk/call-ended/', data=json.dumps(
                {'call_log_id': str(row.id), 'duration': duration, 'answered': answered,
                 'recording_url': f'https://rec.example.com/recordings/{row.id}.wav',
                 'dialed_seconds': 15, 'dial_status': dial_status}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        row.refresh_from_db()
        return row

    def test_paid_missed_call_keeps_its_recording(self):
        row = self.call(False, 0, 'incoming')
        self.assertEqual((row.status, row.revenue), ('no_answer', Decimal('1.00')))
        self.assertEqual(row.recording_url, f'https://rec.example.com/recordings/{row.id}.wav')

    def test_unpaid_missed_call_still_has_none(self):
        row = self.call(False, 0, 'connected')
        self.assertEqual((row.status, row.recording_url), ('no_answer', ''))

    def test_answered_call_unchanged(self):
        row = self.call(True, 42, 'connected', dial_status='ANSWER')
        self.assertTrue(row.recording_url.endswith(f'{row.id}.wav'))

    @override_settings(PAID_MISSED_RECORDING=False)
    def test_switch_off_keeps_todays_rule(self):
        row = self.call(False, 0, 'incoming')
        self.assertEqual(row.recording_url, '')

    def test_late_answer_gets_its_recording(self):
        from routing.late_answer import mark
        row = self.call(False, 0, 'connected', dial_status='CANCEL')
        CallLog.objects.filter(pk=row.pk).update(block_reason='Not Connected', recording_url='')
        row.refresh_from_db()
        now = timezone.now().timestamp()
        with mock.patch('tasks.retire_used_number.delay'):
            self.assertTrue(mark(row, {'answer_at': now, 'cancel_at': now - 0.2}))
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration), ('completed', 1))
        self.assertEqual(row.recording_url, f'https://app.example.com/recordings/{row.id}.wav')


class RecordFromDialScriptTests(TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location('record_from_dial', 'deploy/record_from_dial.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_changes_only_the_recorder_line_on_server_a(self):
        rd = self.load()
        src = open('docs/asterisk/server-a-from-carrier-2026-11-04.conf').read()
        out = rd.transform(src)
        self.assertNotIn('MixMonitor(${RECFILE},b)', out)
        self.assertIn('MixMonitor(${RECFILE}))   ; record-from-dial (CH-535)', out)
        changed = [(a, b) for a, b in zip(src.splitlines(), out.splitlines()) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertEqual(len(src.splitlines()), len(out.splitlines()))
        with self.assertRaises(rd.Stop):
            rd.transform(out)                                  # never applied twice

    def test_stops_when_the_line_is_not_there(self):
        rd = self.load()
        with self.assertRaises(rd.Stop):
            rd.transform('[from-carrier]\nexten => _X.,1,Dial(PJSIP/x)\n')
