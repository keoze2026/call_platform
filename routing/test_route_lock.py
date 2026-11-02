"""CH-467: buyer concurrency limits are exact - routing decides one call at a
time per workspace, on Server B and in Server A's fallback.

The load test put 267 calls on a buyer limited to 250: with 12 call workers,
several calls read "249 live" at the same moment and all went through.

Run:  python manage.py test routing.test_route_lock
"""
import json
import threading
import time
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing import asterisk_handler
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
LINE = '+18443122521'


@override_settings(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RouteLockTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-02')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        buyer = Buyer.objects.create(organization=self.org, name='Q04')
        Destination.objects.create(organization=self.org, buyer=buyer, name='Q04', tfn=LINE, enabled=True)
        RuleDestination.objects.create(rule=rule, destination=LINE, buyer=buyer)
        for p in (mock.patch.object(RoutingEngine, 'check_balance', return_value=True),
                  mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay')):
            p.start()
            self.addCleanup(p.stop)
        self.key = asterisk_handler._route_lock_key(self.org.id)
        self.n = 0

    def call(self):
        self.n += 1
        return Client().post('/api/twilio/asterisk/route/', data=json.dumps(
            {'caller_number': f'+1555{self.n:07d}', 'called_number': TFN, 'asterisk_call_id': f'u{self.n}'}),
            content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)

    def test_routing_takes_and_releases_the_lock(self):
        seen = {}
        real = RoutingEngine.route_call

        def spy(*a, **k):
            seen['held'] = cache.get(self.key) is not None
            return real(*a, **k)
        with mock.patch.object(RoutingEngine, 'route_call', side_effect=spy):
            self.assertEqual(self.call().json()['action'], 'dial')
        self.assertTrue(seen['held'])                       # held while deciding
        self.assertIsNone(cache.get(self.key))              # released after
        self.assertEqual(CallLog.objects.get(twilio_call_sid='u1').status, 'in_progress')

    def test_a_second_call_waits_for_the_first(self):
        cache.add(self.key, 1, 10)                          # another call is deciding
        threading.Timer(0.3, lambda: cache.delete(self.key)).start()
        started = time.monotonic()
        self.assertEqual(self.call().json()['action'], 'dial')
        self.assertGreaterEqual(time.monotonic() - started, 0.25)

    def test_a_stuck_lock_never_holds_a_call_for_long(self):
        cache.add(self.key, 1, 10)
        with mock.patch.object(asterisk_handler, 'ROUTE_LOCK_WAIT', 0.2):
            started = time.monotonic()
            self.assertEqual(self.call().json()['action'], 'dial')
        self.assertLess(time.monotonic() - started, 2)

    def test_released_when_routing_fails(self):
        with mock.patch.object(RoutingEngine, 'route_call', side_effect=RuntimeError('db down')):
            with self.assertRaises(RuntimeError):
                self.call()
        self.assertIsNone(cache.get(self.key))

    def test_a_broken_cache_does_not_stop_calls(self):
        with mock.patch.object(asterisk_handler.cache, 'add', side_effect=ConnectionError('redis')):
            self.assertEqual(self.call().json()['action'], 'dial')
