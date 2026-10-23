"""CH-239: a test call from TFN Check, placed inside our own phone system and
watched step by step (Sir, 24 Oct: users want to see test calls with their
own eyes; nothing goes to the carrier).

Run:  python manage.py test phone_numbers.test_tfn_internal_call
"""
import json
from unittest import mock

from django.test import override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from phone_numbers import ami, tfn_check, tfn_test
from phone_numbers.models import PhoneNumber, TfnTest
from phone_numbers.test_tfn_test import OK, TEST_CID, Base
from publishers.models import Publisher
from routing.engine import RoutingEngine
from routing.models import CallLog

AMI_ON = dict(ASTERISK_AMI_HOST='10.0.0.1', ASTERISK_AMI_SECRET='x')


def fake_ami():
    fake = mock.MagicMock()
    fake.__enter__.return_value = fake
    fake.originate.return_value = 'a1'
    return fake


@override_settings(**AMI_ON)
class InternalCallTests(Base):
    def make(self):
        return tfn_test.create_test(self.phone, self.admin, internal=True)

    def keys(self, t):
        return [(s['key'], s['ok']) for s in t.steps]

    def test_the_call_goes_through_our_own_dialplan_and_passes(self):
        t = self.make()
        self.assertEqual(t.mode, 'internal_call')
        fake = fake_ami()

        def asterisk_runs_the_dialplan(*a, **k):
            # What [from-carrier] does with the call: the route request.
            with mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
                reply = self.inbound(called='18774893792').json()
            self.assertEqual(reply, {'action': 'hangup', 'reason': 'tfn_test'})
            return 'a1'
        fake.originate.side_effect = asterisk_runs_the_dialplan
        with mock.patch.object(ami, 'AMIClient', return_value=fake):
            tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertEqual((t.status, t.mode), ('passed', 'internal_call'))
        fake.originate.assert_called_once_with('Local/18774893792@from-carrier', TEST_CID)
        self.assertEqual(sorted(self.keys(t)), sorted([
            ('reached', True), ('number', True), ('campaign', True), ('routing', True), ('placed', True)]))
        self.assertFalse(CallLog.objects.exists())  # no call row, no buyer, no fee

    def test_the_steps_name_no_buyer(self):
        t = self.make()
        fake = fake_ami()
        fake.originate.side_effect = lambda *a, **k: (
            mock.patch.object(RoutingEngine, 'route_call', return_value=OK).start(),
            tfn_test.handle_inbound('+18774893792', TEST_CID), mock.patch.stopall())
        with mock.patch.object(ami, 'AMIClient', return_value=fake):
            tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertNotIn('Acme', json.dumps(t.steps))
        self.assertIn('Acme', t.reason)  # the full story stays in the backend

    def test_a_call_that_never_reaches_routing_fails_and_says_where(self):
        t = self.make()
        with mock.patch.object(ami, 'AMIClient', return_value=fake_ami()), \
                mock.patch.object(tfn_test, 'WAIT_SECONDS', 0.01):
            tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertEqual(t.status, 'failed')
        self.assertIn(('placed', True), self.keys(t))
        self.assertIn(('reached', False), self.keys(t))

    def test_a_broken_routing_shows_the_failing_step(self):
        PhoneNumber.objects.filter(pk=self.phone.pk).update(campaign=None)
        t = self.make()
        fake = fake_ami()
        fake.originate.side_effect = lambda *a, **k: tfn_test.handle_inbound('+18774893792', TEST_CID)
        with mock.patch.object(ami, 'AMIClient', return_value=fake):
            tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertEqual(t.status, 'failed')
        self.assertIn(('campaign', False), self.keys(t))

    def test_an_unreachable_switch_falls_back_to_our_checks_and_says_so(self):
        t = self.make()
        with mock.patch.object(ami, 'AMIClient', side_effect=ami.AMIError('cannot reach Asterisk manager')), \
                mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
            tfn_test.run(t.pk)
        t.refresh_from_db()
        self.assertEqual((t.status, t.mode), ('passed', 'platform_check'))
        self.assertIn(('placed', False), self.keys(t))
        self.assertIn(('routing', True), self.keys(t))

    def test_tfn_check_status_still_comes_from_real_calls_only(self):
        t = self.make()
        TfnTest.objects.filter(pk=t.pk).update(status='passed')
        result = tfn_check.check(self.org, '18774893792')['results'][0]
        self.assertEqual(result['status'], 'active')


@override_settings(**AMI_ON)
class TestCallEndpointTests(Base):
    def post(self, client, number):
        with mock.patch('tasks.run_tfn_test.delay') as delay:
            r = client.post('/api/numbers/check/test', data=json.dumps({'number': number}),
                            content_type='application/json')
        return r, delay

    def test_start_and_watch_a_test_call(self):
        r, delay = self.post(self.client, '+1 877 489 3792')
        self.assertEqual(r.status_code, 202, r.content)
        body = r.json()
        self.assertEqual((body['status'], body['mode']), ('queued', 'internal_call'))
        delay.assert_called_once_with(body['id'])
        tfn_test.add_steps(body['id'], tfn_test._step('placed', True, 'Test call placed'))
        seen = self.client.get(f"/api/numbers/check/test/{body['id']}").json()
        self.assertEqual(seen['steps'][0]['text'], 'Test call placed')

    def test_a_second_test_while_one_runs_is_refused(self):
        self.post(self.client, '18774893792')
        r, delay = self.post(self.client, '18774893792')
        self.assertEqual(r.status_code, 400)
        delay.assert_not_called()

    def test_unknown_number(self):
        r, _ = self.post(self.client, '18005550000')
        self.assertEqual(r.status_code, 404)

    def test_a_publisher_tests_only_their_own_numbers(self):
        mine = Publisher.objects.create(organization=self.org, name='P1')
        login = User.objects.create(username='p1', email='p1@x.io', organization=self.org,
                                    role='publisher', publisher=mine, is_active=True)
        from django.test import Client
        client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(login)}')
        r, _ = self.post(client, '18774893792')
        self.assertEqual(r.status_code, 404)
        r, _ = self.post(self.client, '18774893792')
        self.assertEqual(client.get(f"/api/numbers/check/test/{r.json()['id']}").status_code, 404)
        PhoneNumber.objects.filter(pk=self.phone.pk).update(publisher=mine)
        TfnTest.objects.all().delete()
        r, _ = self.post(client, '18774893792')
        self.assertEqual(r.status_code, 202)
