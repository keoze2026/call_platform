"""CH-213: TFN Check - our own version of XOLO's number lookup.

Run:  python manage.py test phone_numbers.test_tfn_check
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from phone_numbers import tfn_check
from phone_numbers.models import PhoneNumber, TfnCheckLink, TfnTest
from publishers.models import Publisher
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


class ParseTests(TestCase):
    def test_every_common_way_of_writing_a_number(self):
        numbers, invalid = tfn_check.parse_numbers(
            '+1 (888) 891-0563\n18888910564, 8888910565; 8888910566 8888910567\n+1 888 891 0568')
        self.assertEqual(numbers, ['8888910563', '8888910564', '8888910565',
                                   '8888910566', '8888910567', '8888910568'])
        self.assertEqual(invalid, [])

    def test_duplicates_are_dropped_and_junk_reported(self):
        numbers, invalid = tfn_check.parse_numbers('8888910563\n+18888910563\n12345\n28888910563')
        self.assertEqual(numbers, ['8888910563'])
        self.assertEqual(invalid, ['12345', '28888910563'])

    def test_a_list_works_too(self):
        self.assertEqual(tfn_check.parse_numbers(['+18888910563', '888-891-0599'])[0],
                         ['8888910563', '8888910599'])


@override_settings(CACHES=CACHE)
class JudgeTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='PDSO', status='active')
        self.n = 0

    def number(self, digits, status='active', campaign='default', publisher=None):
        return PhoneNumber.objects.create(
            organization=self.org, number='+1' + digits, vendor='Other', status=status,
            campaign=self.camp if campaign == 'default' else campaign, publisher=publisher)

    def call(self, digits, hours_ago=0.1):
        self.n += 1
        row = CallLog.objects.create(organization=self.org, campaign=self.camp,
                                     caller_number='+15551234567', called_number='+1' + digits,
                                     twilio_call_sid=f'sid-{self.n}', status='completed')
        CallLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=hours_ago))

    def add_test(self, phone, passed, hours_ago=0.5, reason=None):
        when = timezone.now() - timedelta(hours=hours_ago)
        t = TfnTest.objects.create(
            organization=self.org, phone_number=phone, number=phone.number,
            mode=TfnTest.Mode.REAL_CALL, status='passed' if passed else 'failed',
            reason=reason or ('Call reached the platform.' if passed else
                              'No call reached the platform within 30s. The carrier did not deliver this number to us.'),
            finished_at=when)
        TfnTest.objects.filter(pk=t.pk).update(created_at=when)

    def status(self, digits, publisher=None):
        [r] = tfn_check.check(self.org, digits, publisher)['results']
        return r['status']

    def test_not_on_the_platform(self):
        self.assertEqual(self.status('8888910500'), 'not_found')

    def test_retired_paused_and_off_campaign(self):
        self.number('8888910501', status='released')
        self.number('8888910502', status='paused')
        self.number('8888910503', campaign=None)
        off = Campaign.objects.create(organization=self.org, name='Off', status='paused')
        self.number('8888910504', campaign=off)
        self.assertEqual(self.status('8888910501'), 'expired')
        self.assertEqual(self.status('8888910502'), 'paused')
        self.assertEqual(self.status('8888910503'), 'not_in_service')
        self.assertEqual(self.status('8888910504'), 'not_in_service')

    def test_a_retired_copy_does_not_hide_the_live_number(self):
        # The same number added twice, written two ways (the column is unique).
        PhoneNumber.objects.create(organization=self.org, number='18888910505', vendor='Other',
                                   status='released')
        self.number('8888910505')
        self.call('8888910505')
        self.assertEqual(self.status('8888910505'), 'working')

    def test_a_recent_call_proves_it_works(self):
        self.number('8888910506')
        self.call('8888910506', hours_ago=2)
        r = tfn_check.check(self.org, '8888910506')['results'][0]
        self.assertEqual(r['status'], 'working')
        self.assertEqual(r['detail'], 'Last call received 2 hours ago.')

    def test_a_passed_test_call_proves_it_works(self):
        phone = self.number('8888910507')
        self.add_test(phone, passed=True)
        self.assertEqual(self.status('8888910507'), 'working')

    def test_a_test_call_the_carrier_did_not_deliver(self):
        phone = self.number('8888910508')
        self.call('8888910508', hours_ago=5)
        self.add_test(phone, passed=False, hours_ago=1)
        self.assertEqual(self.status('8888910508'), 'not_working')

    def test_a_real_call_after_a_failed_test_wins(self):
        phone = self.number('8888910509')
        self.add_test(phone, passed=False, hours_ago=3)
        self.call('8888910509', hours_ago=1)
        self.assertEqual(self.status('8888910509'), 'working')

    def test_a_platform_refusal_is_not_a_dead_number(self):
        phone = self.number('8888910510')
        self.add_test(phone, passed=False, reason='Routing would refuse the call: no destination')
        self.assertEqual(self.status('8888910510'), 'active')

    def test_quiet_but_live(self):
        self.number('8888910511')
        self.call('8888910511', hours_ago=30)
        self.assertEqual(self.status('8888910511'), 'active')

    def test_a_publisher_sees_only_its_own_numbers(self):
        a = Publisher.objects.create(organization=self.org, name='PUB A')
        b = Publisher.objects.create(organization=self.org, name='PUB B')
        self.number('8888910512', publisher=a)
        self.number('8888910513', publisher=b)
        self.assertEqual(self.status('8888910512', publisher=a), 'active')
        self.assertEqual(self.status('8888910513', publisher=a), 'not_found')

    def test_another_workspaces_number_is_not_found(self):
        other = Organization.objects.create(name='Other', slug='other')
        PhoneNumber.objects.create(organization=other, number='+18888910514', vendor='Other')
        self.assertEqual(self.status('8888910514'), 'not_found')

    def test_summary_counts_every_result(self):
        self.number('8888910515')
        out = tfn_check.check(self.org, '8888910515\n8888910516\n12345')
        self.assertEqual(out['summary']['active'], 1)
        self.assertEqual(out['summary']['not_found'], 1)
        self.assertEqual(out['invalid'], ['12345'])

    def test_at_most_200_numbers_a_check(self):
        out = tfn_check.check(self.org, '\n'.join(f'888{i:07d}' for i in range(250)))
        self.assertEqual(len(out['results']), 200)
        self.assertTrue(out['truncated'])


@override_settings(ALLOWED_HOSTS=['*'], CACHES=CACHE, FRONTEND_URL='https://app.avortyx.io')
class ApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(username='owner', email='owner@x.io', organization=self.org,
                                         role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.camp = Campaign.objects.create(organization=self.org, name='PDSO', status='active')
        self.phone = PhoneNumber.objects.create(organization=self.org, number='+18888910563',
                                                vendor='Other', campaign=self.camp)
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def post(self, url, data, client=None):
        import json
        return (client or self.api).post(url, data=json.dumps(data), content_type='application/json')

    def test_the_portal_check(self):
        r = self.post('/api/numbers/check/', {'numbers': '+1 888 891 0563\n8888910000'})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([x['status'] for x in body['results']], ['active', 'not_found'])
        self.assertEqual(body['tests_queued'], 0)

    def test_the_portal_can_queue_test_calls(self):
        with mock.patch('tasks.run_tfn_test.apply_async') as run:
            r = self.post('/api/numbers/check/', {'numbers': '8888910563\n8888910000', 'test': True})
        self.assertEqual(r.json()['tests_queued'], 1)
        run.assert_called_once()
        self.assertEqual(TfnTest.objects.get().trigger, 'manual')

    def test_todays_tests_stay_until_eastern_midnight(self):
        # CH-341: the history runs from 12 AM Eastern; yesterday's test is gone.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        et = ZoneInfo('America/New_York')
        now = datetime(2026, 10, 28, 10, 0, tzinfo=et)
        for when, status in ((datetime(2026, 10, 27, 23, 59, tzinfo=et), 'passed'),
                             (datetime(2026, 10, 28, 0, 1, tzinfo=et), 'passed'),
                             (datetime(2026, 10, 28, 9, 0, tzinfo=et), 'failed')):
            t = TfnTest.objects.create(organization=self.org, phone_number=self.phone,
                                       number=self.phone.number, status=status, requested_by=self.owner)
            TfnTest.objects.filter(pk=t.pk).update(created_at=when)
        with mock.patch('django.utils.timezone.now', return_value=now):
            body = self.api.get('/api/numbers/check/today').json()
            r = self.post('/api/numbers/check/', {'numbers': '8888910563'}).json()['results'][0]
        self.assertEqual(body['total'], 2)
        self.assertEqual(body['numbers'][0]['tests'], 2)
        self.assertEqual((body['numbers'][0]['passed'], body['numbers'][0]['failed']), (1, 1))
        self.assertEqual(body['tests'][0]['by'], 'You')
        self.assertTrue(body['since'].startswith('2026-10-28T00:00:00'))
        self.assertEqual((r['tests_today'], r['tests_today_passed'], r['tests_today_failed']), (2, 1, 1))

    def test_a_check_needs_a_login(self):
        r = Client().post('/api/numbers/check/', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_share_links_make_list_and_cancel(self):
        r = self.post('/api/numbers/check/links', {'name': 'For PDSO'})
        self.assertEqual(r.status_code, 201, r.content)
        link = r.json()
        self.assertTrue(link['url'].startswith('https://app.avortyx.io/check/'))
        self.assertGreaterEqual(len(link['token']), 40)
        self.assertEqual([l['id'] for l in self.api.get('/api/numbers/check/links').json()], [link['id']])
        r = self.api.delete(f"/api/numbers/check/links/{link['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['active'])

    def test_a_link_needs_a_name(self):
        self.assertEqual(self.post('/api/numbers/check/links', {'name': '  '}).status_code, 400)

    def test_the_public_link(self):
        link = TfnCheckLink.objects.create(organization=self.org, name='Partner', token='t' * 43)
        anon = Client()
        r = anon.get(f'/api/public/tfn-check/{link.token}')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['name'], 'Partner')
        r = self.post(f'/api/public/tfn-check/{link.token}', {'numbers': ['+18888910563']}, client=anon)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['results'][0]['status'], 'active')
        link.refresh_from_db()
        self.assertEqual(link.use_count, 1)
        self.assertIsNotNone(link.last_used_at)

    def test_a_cancelled_or_unknown_link_answers_nothing(self):
        link = TfnCheckLink.objects.create(organization=self.org, name='Old', token='x' * 43,
                                           revoked_at=timezone.now())
        anon = Client()
        self.assertEqual(anon.get(f'/api/public/tfn-check/{link.token}').status_code, 404)
        self.assertEqual(self.post(f'/api/public/tfn-check/{link.token}', {'numbers': '8888910563'},
                                   client=anon).status_code, 404)
        self.assertEqual(anon.get('/api/public/tfn-check/nope').status_code, 404)

    def test_a_publisher_link_only_answers_for_that_publisher(self):
        pub = Publisher.objects.create(organization=self.org, name='PUB A')
        link = TfnCheckLink.objects.create(organization=self.org, name='A', token='p' * 43, publisher=pub)
        r = self.post(f'/api/public/tfn-check/{link.token}', {'numbers': '8888910563'}, client=Client())
        self.assertEqual(r.json()['results'][0]['status'], 'not_found')

    def test_a_link_is_rate_limited(self):
        link = TfnCheckLink.objects.create(organization=self.org, name='Busy', token='r' * 43)
        with mock.patch.object(tfn_check, 'LINK_LIMIT_PER_MINUTE', 2):
            codes = [self.post(f'/api/public/tfn-check/{link.token}', {'numbers': '8888910563'},
                               client=Client()).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


class RoutePermissionTests(TestCase):
    def test_who_may_use_each_endpoint(self):
        from accounts.route_permissions import requirements
        self.assertEqual(requirements('GET', '/routing/calls/live-counts'), [('campaigns', 'view')])
        self.assertEqual(requirements('POST', '/numbers/check/'), [('tracking-numbers', 'view')])
        self.assertEqual(requirements('POST', '/numbers/check/links'), [('tracking-numbers', 'edit')])
        self.assertEqual(requirements('DELETE', '/numbers/check/links/abc'), [('tracking-numbers', 'delete')])


# 10:00 AM Eastern on a weekday in October (EDT = UTC-4).
MORNING = datetime(2026, 10, 22, 14, 0, tzinfo=dt_timezone.utc)
NIGHT = datetime(2026, 10, 23, 3, 0, tzinfo=dt_timezone.utc)   # 11 PM Eastern


@override_settings(CACHES=CACHE, ASTERISK_AMI_HOST='127.0.0.1', ASTERISK_AMI_SECRET='s',
                   TFN_TEST_CALLER_ID='+18005550100', TFN_AUTO_TEST_ENABLED=True,
                   TFN_AUTO_TEST_HOURS=3, TFN_AUTO_TEST_MAX=20)
class AutoTestTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='PDSO', status='active')
        self.quiet = PhoneNumber.objects.create(organization=self.org, number='+18888910601',
                                                vendor='Other', campaign=self.camp)
        self.busy = PhoneNumber.objects.create(organization=self.org, number='+18888910602',
                                               vendor='Other', campaign=self.camp)
        CallLog.objects.create(organization=self.org, campaign=self.camp, caller_number='+15551234567',
                               called_number='+18888910602', twilio_call_sid='busy-1', status='completed')

    def run_task(self, now):
        from tasks import auto_tfn_tests
        with mock.patch('django.utils.timezone.now', return_value=now), \
                mock.patch('tasks.run_tfn_test.apply_async') as run:
            CallLog.objects.update(created_at=now - timedelta(minutes=10))
            result = auto_tfn_tests()
        return result, run

    def test_only_quiet_numbers_are_tested(self):
        result, run = self.run_task(MORNING)
        self.assertEqual(result, '1 queued')
        test = TfnTest.objects.get()
        self.assertEqual((test.number, test.trigger), ('+18888910601', 'auto'))
        run.assert_called_once()

    def test_not_twice_inside_the_window(self):
        self.run_task(MORNING)
        result, _ = self.run_task(MORNING + timedelta(minutes=30))
        self.assertEqual(result, '0 queued')

    def test_no_test_calls_at_night(self):
        result, run = self.run_task(NIGHT)
        self.assertEqual(result, 'outside 7:30 AM - 9 PM Eastern')
        run.assert_not_called()

    @override_settings(TFN_AUTO_TEST_ENABLED=False)
    def test_off_until_switched_on(self):
        self.assertEqual(self.run_task(MORNING)[0], 'off')


@override_settings(CACHES=CACHE)
class AlertTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.phone = PhoneNumber.objects.create(organization=self.org, number='+18888910701', vendor='Other')

    def failed(self, trigger, reason='No call reached the platform within 30s.'):
        return TfnTest.objects.create(organization=self.org, phone_number=self.phone, number=self.phone.number,
                                      mode='real_call', status='failed', trigger=trigger, reason=reason)

    def test_an_automatic_test_the_carrier_did_not_deliver_alerts_once(self):
        from phone_numbers.tfn_test import _after_real_call
        with mock.patch('notifications.services.NotificationService.dispatch') as send:
            _after_real_call(self.failed('auto').pk)
            _after_real_call(self.failed('auto').pk)
        send.assert_called_once()
        self.assertEqual(send.call_args[0][0], 'number.not_working')
        self.assertEqual(send.call_args[0][2]['message'],
                         '1 TFN Not Working\n+1 888-891-0701\n'
                         'A test call did not reach the platform. Please check or replace this TFN.')

    def test_a_manual_test_or_a_platform_refusal_does_not_alert(self):
        from phone_numbers.tfn_test import _after_real_call
        with mock.patch('notifications.services.NotificationService.dispatch') as send:
            _after_real_call(self.failed('manual').pk)
            _after_real_call(self.failed('auto', reason='Routing would refuse the call').pk)
        send.assert_not_called()

    def test_every_workspace_gets_the_rule(self):
        from notifications.defaults import ensure_default_rules
        from notifications.models import NotificationRule
        User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin')
        ensure_default_rules(self.org)
        self.assertTrue(NotificationRule.objects.filter(
            organization=self.org, event='number.not_working', is_active=True).exists())
