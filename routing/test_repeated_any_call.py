"""CH-230: Repeated counts any earlier call, as Revo's Dupe.

Run:  python manage.py test routing.test_repeated_any_call
"""
import json
from datetime import timedelta
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import Client, override_settings
from django.utils import timezone

from analytics.models import CallRecord
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog
from routing.signals import mirror_call_log
from routing.test_retry_group import Base, CACHE, SECRET

CALLER, TFN = '+15047156000', '+18778186159'


@override_settings(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, CACHES=CACHE)
class RepeatedTests(Base):
    OK = {'destination': '+18667404516', 'rule': None, 'error': None, 'buyer': None}

    def setUp(self):
        super().setUp()
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other', campaign=self.camp)

    def ring(self, sid):
        with mock.patch('tasks.enrich_call_carrier.delay'), \
                mock.patch.object(RoutingEngine, 'route_call', return_value=self.OK):
            r = Client().post('/api/twilio/asterisk/route/',
                              data=json.dumps({'caller_number': CALLER, 'called_number': TFN,
                                               'asterisk_call_id': sid}),
                              content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        return CallLog.objects.get(twilio_call_sid=sid)

    def earlier(self, ago, length=21, status='no_answer'):
        now = timezone.now()
        return self.try_(CALLER, TFN, now - timedelta(seconds=ago),
                         now - timedelta(seconds=ago - length), status=status)

    def test_a_call_after_a_missed_call_is_repeated(self):
        self.earlier(ago=76)
        self.assertTrue(self.ring('second').is_duplicate)

    def test_a_first_call_is_not_repeated(self):
        self.assertFalse(self.ring('first').is_duplicate)

    def test_a_senders_retry_is_not_a_repeat(self):
        self.earlier(ago=24, length=23)           # ended 1 s ago: the sender retries
        new = self.ring('retry')
        self.assertIsNotNone(new.retry_root_id)
        self.assertFalse(new.is_duplicate)

    def test_routing_still_treats_a_missed_caller_as_new(self):
        self.earlier(ago=76)
        self.assertFalse(RoutingEngine.is_duplicate(CALLER, str(self.camp.id), 24))

    def test_qualified_is_unchanged_for_an_answer_after_a_missed_call(self):
        self.earlier(ago=200)
        second = self.earlier(ago=100, length=60, status='completed')
        CallLog.objects.filter(pk=second.pk).update(is_duplicate=True)
        mirror_call_log(second.pk)
        self.assertTrue(CallRecord.objects.get(id=second.pk).is_qualified)

    def test_qualified_is_no_for_a_caller_answered_before(self):
        self.earlier(ago=300, length=60, status='completed')
        again = self.earlier(ago=100, length=60, status='completed')
        mirror_call_log(again.pk)
        self.assertFalse(CallRecord.objects.get(id=again.pk).is_qualified)

    def test_recompute_marks_todays_repeat(self):
        first = self.earlier(ago=200)
        second = self.earlier(ago=100)
        today = timezone.now().astimezone(ZoneInfo('America/New_York')).date().isoformat()
        out = StringIO()
        call_command('recompute_repeated', '--start', today, '--end', today, stdout=out)
        self.assertIn('1 rows change', out.getvalue())
        call_command('recompute_repeated', '--start', today, '--end', today, '--apply', stdout=StringIO())
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.is_duplicate, second.is_duplicate), (False, True))
