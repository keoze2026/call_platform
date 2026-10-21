"""CH-196: a call whose caller ID is two numbers run together still goes out.

Run:  python manage.py test routing.test_long_caller_id
"""
import json
from types import SimpleNamespace
from unittest import mock

from django.test import Client, TestCase, override_settings

from accounts.models import Organization
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.asterisk_handler import normalize_caller
from routing.engine import RoutingEngine
from routing.models import CallLog

SECRET = 'test-secret'
OK = {'destination': '+18779641773', 'rule': None, 'error': None, 'buyer': None}


class NormalizeTests(TestCase):
    def test_a_normal_number_is_unchanged(self):
        for n in ('+14503668132', '14503668132', '4503668132', 'anonymous', ''):
            self.assertEqual(normalize_caller(n), n)

    def test_two_numbers_run_together_keep_the_first(self):
        self.assertEqual(normalize_caller('+120794287412079428749'), '+12079428741')

    def test_a_long_non_north_american_number_is_cut_to_15_digits(self):
        self.assertEqual(normalize_caller('+4412345678901234567'), '+441234567890123')


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class LongCallerRoutesTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=org, name='C-02')
        PhoneNumber.objects.create(organization=org, number='+18778183598', vendor='Other',
                                   campaign=self.campaign)

    def route(self, caller):
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'), \
                mock.patch.object(RoutingEngine, 'route_call', return_value=OK):
            r = Client().post('/api/twilio/asterisk/route/',
                              data=json.dumps({'caller_number': caller, 'called_number': '+18778183598',
                                               'asterisk_call_id': 'sid-long'}),
                              content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_the_call_goes_out_to_the_buyer(self):
        out = self.route('+120794287412079428749')
        self.assertEqual((out['action'], out['buyer_number']), ('dial', '+18779641773'))
        row = CallLog.objects.get(twilio_call_sid='sid-long')
        self.assertEqual(row.caller_number, '+12079428741')
        self.assertEqual(row.caller_area_code, '207')
