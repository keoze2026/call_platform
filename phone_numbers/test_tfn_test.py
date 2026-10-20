"""TFN test call (CH-175): is this tracking number alive."""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from phone_numbers import ami, tfn_test
from phone_numbers.models import PhoneNumber, TfnTest
from routing.engine import RoutingEngine
from routing.models import CallLog

SECRET = 'test-secret'
TEST_CID = '+15550001234'
NO_BUYER = {'destination': '+15557654321', 'rule': None, 'error': None, 'buyer': None}
OK = {'destination': '+15557654321', 'rule': None, 'error': None,
      'buyer': SimpleNamespace(name='Acme')}


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, TFN_TEST_CALLER_ID=TEST_CID,
    ASTERISK_AMI_HOST='', ASTERISK_AMI_SECRET='',
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class Base(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(
            username='adm', email='adm@x.io', organization=self.org, role='admin')
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-03')
        self.phone = PhoneNumber.objects.create(
            organization=self.org, number='+18774893792', vendor='Other', campaign=self.campaign)

    def running_real_test(self, **kw):
        return TfnTest.objects.create(
            organization=self.org, phone_number=self.phone, number=self.phone.number,
            status='running', mode='real_call', started_at=timezone.now(), **kw)

    def inbound(self, caller=TEST_CID, called='+18774893792'):
        # The normal path queues carrier lookup and fee jobs; no broker here.
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'):
            return self._post_inbound(caller, called)

    def _post_inbound(self, caller, called):
        return self.client.post(
            '/api/twilio/asterisk/route/',
            data=json.dumps({'caller_number': caller, 'called_number': called,
                             'asterisk_call_id': 'sid-1'}),
            content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)


class EvaluateTests(Base):
    def test_active_number_with_live_routing_passes(self):
        with mock.patch.object(RoutingEngine, 'route_call', return_value=OK) as rc:
            ok, why = tfn_test.evaluate(self.phone, TEST_CID)
        self.assertTrue(ok)
        self.assertIn('Acme', why)
        self.assertTrue(rc.call_args[0][1]['is_test'])

    def test_released_number_fails(self):
        self.phone.status = 'released'
        ok, why = tfn_test.evaluate(self.phone, TEST_CID)
        self.assertFalse(ok)
        self.assertIn('released', why)

    def test_number_without_campaign_fails(self):
        self.phone.campaign = None
        ok, why = tfn_test.evaluate(self.phone, TEST_CID)
        self.assertEqual((ok, why), (False, 'Number is not attached to a campaign'))

    def test_paused_campaign_fails(self):
        self.campaign.status = 'paused'
        ok, why = tfn_test.evaluate(self.phone, TEST_CID)
        self.assertFalse(ok)
        self.assertIn('paused', why)

    def test_routing_refusal_is_reported(self):
        with mock.patch.object(RoutingEngine, 'route_call',
                               return_value={'destination': None, 'error': 'Campaign cap reached'}):
            ok, why = tfn_test.evaluate(self.phone, TEST_CID)
        self.assertFalse(ok)
        self.assertIn('Campaign cap reached', why)

    def test_a_test_does_not_take_the_round_robin_turn(self):
        from django.core.cache import cache
        rule = SimpleNamespace(id='r1', destinations=SimpleNamespace(
            all=lambda: SimpleNamespace(order_by=lambda *_: ['a', 'b'])))
        RoutingEngine.get_round_robin_destination(rule, {'is_test': True})
        self.assertIsNone(cache.get('round_robin_r1'))
        RoutingEngine.get_round_robin_destination(rule, {})
        self.assertEqual(cache.get('round_robin_r1'), 1)


class InboundTests(Base):
    def test_the_test_call_is_closed_without_a_call_log_or_a_buyer(self):
        t = self.running_real_test()
        with mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
            r = self.inbound()
        self.assertEqual(r.json(), {'action': 'hangup', 'reason': 'tfn_test'})
        t.refresh_from_db()
        self.assertEqual(t.status, 'passed')
        self.assertIn('reached the platform', t.reason)
        self.assertEqual(CallLog.objects.count(), 0)

    def test_inbound_failure_is_recorded_with_its_reason(self):
        t = self.running_real_test()
        PhoneNumber.objects.filter(pk=self.phone.pk).update(campaign=None)
        self.inbound()
        t.refresh_from_db()
        self.assertEqual(t.status, 'failed')
        self.assertIn('campaign', t.reason)

    def test_same_caller_with_no_pending_test_is_a_normal_call(self):
        with mock.patch.object(RoutingEngine, 'route_call', return_value=NO_BUYER):
            self.inbound()
        self.assertEqual(CallLog.objects.count(), 1)

    def test_other_callers_are_never_taken_for_the_test(self):
        t = self.running_real_test()
        with mock.patch.object(RoutingEngine, 'route_call', return_value=NO_BUYER):
            self.inbound(caller='+15559998888')
        t.refresh_from_db()
        self.assertEqual(t.status, 'running')
        self.assertEqual(CallLog.objects.count(), 1)

    def test_a_stale_test_is_not_matched(self):
        t = self.running_real_test()
        TfnTest.objects.filter(pk=t.pk).update(
            started_at=timezone.now() - timedelta(seconds=120))
        with mock.patch.object(RoutingEngine, 'route_call', return_value=NO_BUYER):
            self.inbound()
        self.assertEqual(CallLog.objects.count(), 1)

    def test_second_invite_cannot_close_a_test_twice(self):
        t = self.running_real_test()
        with mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
            self.inbound()
            t.refresh_from_db()
            first_reason = t.reason
        with mock.patch.object(RoutingEngine, 'route_call', return_value=NO_BUYER):
            self.inbound()
        t.refresh_from_db()
        self.assertEqual((t.status, t.reason), ('passed', first_reason))


class RunTests(Base):
    def make(self):
        return tfn_test.create_test(self.phone, self.admin)

    def test_without_the_manager_link_it_is_a_platform_check(self):
        t = self.make()
        self.assertEqual(t.mode, 'platform_check')
        with mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
            tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertEqual((t.status, t.mode), ('passed', 'platform_check'))
        self.assertTrue(t.reason.startswith('Platform check only.'))
        self.assertIsNotNone(t.finished_at)

    def test_released_number_fails_without_trying_a_call(self):
        PhoneNumber.objects.filter(pk=self.phone.pk).update(status='released')
        t = self.make()
        tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertEqual(t.status, 'failed')
        self.assertIn('released', t.reason)

    @override_settings(ASTERISK_AMI_HOST='10.0.0.1', ASTERISK_AMI_SECRET='x')
    def test_real_call_times_out_when_nothing_arrives(self):
        t = self.make()
        self.assertEqual(t.mode, 'real_call')
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.originate.return_value = 'a1'
        fake.wait_event.return_value = {'Response': 'Failure', 'Reason': '8'}
        with mock.patch.object(ami, 'AMIClient', return_value=fake), \
                mock.patch.object(tfn_test, 'WAIT_SECONDS', 0.01):
            tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertEqual(t.status, 'failed')
        self.assertIn('did not deliver', t.reason)
        self.assertIn('congestion', t.reason)
        fake.originate.assert_called_once_with('PJSIP/18774893792@carrier', TEST_CID)

    @override_settings(ASTERISK_AMI_HOST='10.0.0.1', ASTERISK_AMI_SECRET='x')
    def test_real_call_succeeds_when_the_inbound_leg_closes_it(self):
        t = self.make()
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.originate.return_value = 'a1'

        def inbound_arrives(*a, **k):
            with mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
                tfn_test.handle_inbound('+18774893792', TEST_CID)
            return None
        fake.wait_event.side_effect = inbound_arrives
        with mock.patch.object(ami, 'AMIClient', return_value=fake):
            tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertEqual((t.status, t.mode), ('passed', 'real_call'))

    @override_settings(ASTERISK_AMI_HOST='10.0.0.1', ASTERISK_AMI_SECRET='x')
    def test_unreachable_manager_falls_back_and_says_so(self):
        t = self.make()
        with mock.patch.object(ami, 'AMIClient', side_effect=ami.AMIError('cannot reach Asterisk manager')), \
                mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
            tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertEqual((t.status, t.mode), ('passed', 'platform_check'))
        self.assertIn('Real call not placed', t.reason)

    @override_settings(ASTERISK_AMI_HOST='10.0.0.1', ASTERISK_AMI_SECRET='x', TFN_TEST_CALLER_ID='')
    def test_missing_caller_id_falls_back(self):
        t = self.make()
        with mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
            tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertIn('TFN_TEST_CALLER_ID', t.reason)


class ApiTests(Base):
    def test_start_test_queues_and_returns_202(self):
        with mock.patch('tasks.run_tfn_test.delay') as d:
            r = self.client.post(f'/api/numbers/{self.phone.id}/test')
        self.assertEqual(r.status_code, 202)
        body = r.json()
        self.assertEqual(body['status'], 'queued')
        self.assertEqual(body['number'], '+18774893792')
        d.assert_called_once_with(body['id'])

    def test_second_start_while_one_is_running_is_refused(self):
        with mock.patch('tasks.run_tfn_test.delay'):
            self.client.post(f'/api/numbers/{self.phone.id}/test')
            r = self.client.post(f'/api/numbers/{self.phone.id}/test')
        self.assertEqual(r.status_code, 400)

    def test_daily_limit(self):
        for _ in range(tfn_test.PER_NUMBER_PER_DAY):
            TfnTest.objects.create(
                organization=self.org, phone_number=self.phone, number=self.phone.number,
                status='passed', finished_at=timezone.now())
        with mock.patch('tasks.run_tfn_test.delay'):
            r = self.client.post(f'/api/numbers/{self.phone.id}/test')
        self.assertEqual(r.status_code, 429)

    def test_another_organisations_number_is_not_found(self):
        other = Organization.objects.create(name='Other', slug='oth')
        theirs = PhoneNumber.objects.create(organization=other, number='+18005550100', vendor='Other')
        r = self.client.post(f'/api/numbers/{theirs.id}/test')
        self.assertEqual(r.status_code, 404)

    def test_buyer_login_cannot_test_a_number(self):
        buyer = User.objects.create(
            username='by', email='by@x.io', organization=self.org, role='buyer')
        c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(buyer)}')
        r = c.post(f'/api/numbers/{self.phone.id}/test')
        self.assertIn(r.status_code, (403, 404))

    def test_history_and_single_test_and_recent(self):
        t = TfnTest.objects.create(
            organization=self.org, phone_number=self.phone, number=self.phone.number,
            status='failed', reason='No call reached', finished_at=timezone.now())
        self.assertEqual(self.client.get(f'/api/numbers/{self.phone.id}/tests').json()[0]['id'], str(t.id))
        self.assertEqual(self.client.get(f'/api/numbers/tests/{t.id}').json()['reason'], 'No call reached')
        self.assertEqual(len(self.client.get('/api/numbers/tests/recent?failed_only=true').json()), 1)
        self.assertEqual(self.client.get('/api/numbers/tests/not-a-uuid').status_code, 404)

    def test_daily_test_toggle(self):
        r = self.client.put(f'/api/numbers/{self.phone.id}/daily-test',
                            data=json.dumps({'enabled': True}), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['daily_test_enabled'])
        self.phone.refresh_from_db()
        self.assertTrue(self.phone.daily_test_enabled)


class MorningTests(Base):
    def run_at(self, hour, minute=0):
        import tasks
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime(2026, 10, 21, hour, minute, tzinfo=ZoneInfo('America/New_York'))
        with mock.patch('django.utils.timezone.now', return_value=now), \
                mock.patch('tasks.run_tfn_test.apply_async') as ap:
            tasks.morning_tfn_tests()
        return ap

    def test_only_opted_in_active_numbers_are_tested_after_7(self):
        PhoneNumber.objects.filter(pk=self.phone.pk).update(daily_test_enabled=True)
        PhoneNumber.objects.create(organization=self.org, number='+18774893793', vendor='Other')
        ap = self.run_at(7, 5)
        self.assertEqual(ap.call_count, 1)
        self.assertEqual(TfnTest.objects.get().trigger, 'morning')

    def test_nothing_before_7_and_only_once_a_day(self):
        PhoneNumber.objects.filter(pk=self.phone.pk).update(daily_test_enabled=True)
        self.assertEqual(self.run_at(6, 50).call_count, 0)
        self.assertEqual(self.run_at(7, 0).call_count, 1)
        self.assertEqual(self.run_at(7, 10).call_count, 0)

    def test_released_numbers_are_skipped(self):
        PhoneNumber.objects.filter(pk=self.phone.pk).update(daily_test_enabled=True, status='released')
        self.assertEqual(self.run_at(8).call_count, 0)


class AMIClientTests(TestCase):
    def test_configured_needs_host_and_secret(self):
        with override_settings(ASTERISK_AMI_HOST='h', ASTERISK_AMI_SECRET=''):
            self.assertFalse(ami.configured())
        with override_settings(ASTERISK_AMI_HOST='h', ASTERISK_AMI_SECRET='s'):
            self.assertTrue(ami.configured())

    def test_read_block_parses_headers(self):
        sock = mock.MagicMock()
        buf = bytearray(b'Response: Success\r\nActionID: a\r\n\r\nrest')
        import time
        msg = ami._read_block(sock, buf, time.monotonic() + 1)
        self.assertEqual(msg, {'Response': 'Success', 'ActionID': 'a'})
        self.assertEqual(bytes(buf), b'rest')
