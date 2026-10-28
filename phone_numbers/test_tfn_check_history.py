"""CH-342: TFN Check keeps today's checks (not only test calls) until 12 AM
Eastern, on the portal and on share links.

Run:  python manage.py test phone_numbers.test_tfn_check_history
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from phone_numbers import tfn_check
from phone_numbers.models import PhoneNumber, TfnCheckEvent, TfnCheckLink, TfnTest
from publishers.models import Publisher

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(ALLOWED_HOSTS=['*'], CACHES=CACHE)
class TodayChecksTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.owner = User.objects.create(username='owner', email='owner@x.io', organization=self.org,
                                         role='admin', is_superuser=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.camp = Campaign.objects.create(organization=self.org, name='PDSO', status='active')
        self.pub = Publisher.objects.create(organization=self.org, name='PUB A')
        self.mine = PhoneNumber.objects.create(organization=self.org, number='+18888910563', vendor='Other',
                                               campaign=self.camp, publisher=self.pub)
        self.other = PhoneNumber.objects.create(organization=self.org, number='+18888910564', vendor='Other',
                                                campaign=self.camp)
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def post(self, url, data, client=None):
        return (client or self.api).post(url, data=json.dumps(data), content_type='application/json')

    def rows(self, url, client=None):
        r = (client or self.api).get(url)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json(), {n['number']: n for n in r.json()['numbers']}

    def test_each_check_is_counted_per_number(self):
        for _ in range(3):
            self.post('/api/numbers/check/', {'numbers': '8888910563'})
        self.post('/api/numbers/check/', {'numbers': '8888910564\n8888910000'})
        body, by = self.rows('/api/numbers/check/today')
        self.assertEqual(by['+18888910563']['checks'], 3)
        self.assertEqual(by['+18888910563']['last_result'], 'active')
        self.assertEqual(by['+18888910564']['checks'], 1)
        self.assertEqual(by['+18888910000']['last_result'], 'not_found')
        self.assertEqual(body['total_checks'], 5)
        self.assertEqual(by['+18888910563']['tests'], 0)
        self.assertIsNone(by['+18888910563']['last_status'])
        self.assertEqual(body['numbers'][-1]['number'], '+18888910563')   # newest first

    def test_checks_and_test_calls_on_one_row(self):
        self.post('/api/numbers/check/', {'numbers': '8888910563'})
        TfnTest.objects.create(organization=self.org, phone_number=self.mine, number='+18888910563',
                               status='passed', requested_by=self.owner)
        TfnTest.objects.create(organization=self.org, phone_number=self.mine, number='+18888910563',
                               status='failed', requested_by=self.owner)
        body, by = self.rows('/api/numbers/check/today')
        row = by['+18888910563']
        self.assertEqual((row['checks'], row['tests'], row['passed'], row['failed']), (1, 2, 1, 1))
        self.assertEqual(body['total'], 2)
        self.assertEqual(body['tests'][0]['by'], 'You')

    def test_a_number_only_tested_still_shows(self):
        TfnTest.objects.create(organization=self.org, phone_number=self.mine, number='+18888910563',
                               status='passed', trigger='auto')
        _, by = self.rows('/api/numbers/check/today')
        self.assertEqual((by['+18888910563']['tests'], by['+18888910563']['checks']), (1, 0))

    def test_yesterday_is_gone_after_midnight_eastern(self):
        self.post('/api/numbers/check/', {'numbers': '8888910563'})
        TfnCheckEvent.objects.update(created_at=tfn_check.eastern_day_start() - timedelta(minutes=1))
        body, _ = self.rows('/api/numbers/check/today')
        self.assertEqual(body['numbers'], [])

    def test_a_share_link_sees_its_own_checks_and_their_test_calls(self):
        link = TfnCheckLink.objects.create(organization=self.org, name='Partner', token='t' * 43)
        other = TfnCheckLink.objects.create(organization=self.org, name='Other', token='o' * 43)
        anon = Client()
        self.post(f'/api/public/tfn-check/{link.token}', {'numbers': '8888910563'}, client=anon)
        self.post(f'/api/public/tfn-check/{link.token}', {'numbers': '8888910563'}, client=anon)
        self.post(f'/api/public/tfn-check/{other.token}', {'numbers': '8888910564'}, client=anon)
        TfnTest.objects.create(organization=self.org, phone_number=self.mine, number='+18888910563',
                               status='passed', requested_by=self.owner)
        TfnTest.objects.create(organization=self.org, phone_number=self.other, number='+18888910564',
                               status='passed', requested_by=self.owner)
        body, by = self.rows(f'/api/public/tfn-check/{link.token}/today', client=anon)
        self.assertEqual(list(by), ['+18888910563'])
        self.assertEqual((by['+18888910563']['checks'], by['+18888910563']['tests']), (2, 1))
        # A partner never sees who on our side ran the test.
        self.assertEqual(body['tests'][0]['by'], 'Avortyx')
        # Staff see every check.
        _, staff = self.rows('/api/numbers/check/today')
        self.assertEqual(staff['+18888910564']['checks'], 1)

    def test_a_cancelled_link_has_no_list(self):
        link = TfnCheckLink.objects.create(organization=self.org, name='Old', token='x' * 43,
                                           revoked_at=timezone.now())
        self.assertEqual(Client().get(f'/api/public/tfn-check/{link.token}/today').status_code, 404)

    def test_a_publisher_link_never_counts_other_numbers(self):
        link = TfnCheckLink.objects.create(organization=self.org, name='A', token='p' * 43, publisher=self.pub)
        self.post(f'/api/public/tfn-check/{link.token}', {'numbers': '8888910563\n8888910564'}, client=Client())
        TfnTest.objects.create(organization=self.org, phone_number=self.other, number='+18888910564',
                               status='passed')
        _, by = self.rows(f'/api/public/tfn-check/{link.token}/today', client=Client())
        self.assertEqual(by['+18888910564']['last_result'], 'not_found')
        self.assertEqual(by['+18888910564']['tests'], 0)

    def test_a_publisher_login_sees_only_its_publisher(self):
        login = User.objects.create(username='pub', email='p@x.io', organization=self.org,
                                    role='publisher', publisher=self.pub)
        pub_api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(login)}')
        self.post('/api/numbers/check/', {'numbers': '8888910564'})
        self.post('/api/numbers/check/', {'numbers': '8888910563'}, client=pub_api)
        _, by = self.rows('/api/numbers/check/today', client=pub_api)
        self.assertEqual(list(by), ['+18888910563'])

    def test_a_failed_record_never_breaks_the_check(self):
        with self.assertLogs('phone_numbers.tfn_check', 'ERROR'), \
                mock.patch('phone_numbers.models.TfnCheckEvent.objects.bulk_create', side_effect=RuntimeError):
            r = self.post('/api/numbers/check/', {'numbers': '8888910563'})
        self.assertEqual(r.status_code, 200)


@override_settings(ALLOWED_HOSTS=['*'], CACHES=CACHE)
class WhoRanTheTestTests(TestCase):
    """CH-343: who ran each test call - main account, team, publisher, automatic."""

    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.main = User.objects.create(username='main', email='main@x.io', organization=self.org,
                                        role='admin', first_name='Sir')
        self.team = User.objects.create(username='team', email='team@x.io', organization=self.org,
                                        role='admin', first_name='Evans')
        self.camp = Campaign.objects.create(organization=self.org, name='PDSO', status='active')
        self.pub = Publisher.objects.create(organization=self.org, name='Crug')
        self.pub_login = User.objects.create(username='crug', email='c@x.io', organization=self.org,
                                             role='publisher', publisher=self.pub, first_name='Ann')
        self.phone = PhoneNumber.objects.create(organization=self.org, number='+18888910563', vendor='Other',
                                                campaign=self.camp, publisher=self.pub)
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test(self, user=None, trigger='manual', minutes_ago=0):
        t = TfnTest.objects.create(organization=self.org, phone_number=self.phone, number=self.phone.number,
                                   status='passed', requested_by=user, trigger=trigger)
        TfnTest.objects.filter(pk=t.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))

    def get(self, user, url='/api/numbers/check/today'):
        api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}') if user else Client()
        r = api.get(url)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_staff_see_who_ran_each_test(self):
        self.test(self.main, minutes_ago=5)
        self.test(self.team, minutes_ago=4)
        self.test(self.pub_login, minutes_ago=3)
        self.test(None, trigger='auto', minutes_ago=2)
        self.test(None, trigger='morning', minutes_ago=1)
        body = self.get(self.team)
        got = [(t['by_type'], t['by']) for t in body['tests']]
        self.assertEqual(got, [('morning', 'Morning'), ('auto', 'Automatic'), ('publisher', 'Crug (Ann)'),
                               ('team', 'You'), ('main', 'Sir')])
        [row] = body['numbers']
        self.assertEqual((row['last_by_type'], row['last_by']), ('morning', 'Morning'))

    def test_a_publisher_sees_only_avortyx_for_our_team(self):
        self.test(self.main, minutes_ago=3)
        self.test(self.team, minutes_ago=2)
        self.test(self.pub_login, minutes_ago=1)
        got = [(t['by_type'], t['by']) for t in self.get(self.pub_login)['tests']]
        self.assertEqual(got, [('publisher', 'You'), ('team', 'Avortyx'), ('main', 'Avortyx')])

    def test_a_share_link_never_names_our_team_or_another_publisher(self):
        link = TfnCheckLink.objects.create(organization=self.org, name='Buyer X', token='b' * 43)
        Client().post(f'/api/public/tfn-check/{link.token}', data=json.dumps({'numbers': '8888910563'}),
                      content_type='application/json')
        self.test(self.main, minutes_ago=2)
        self.test(self.pub_login, minutes_ago=1)
        got = [(t['by_type'], t['by']) for t in self.get(None, f'/api/public/tfn-check/{link.token}/today')['tests']]
        self.assertEqual(got, [('publisher', 'Publisher'), ('main', 'Avortyx')])
