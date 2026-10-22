"""CH-215: a sender's retries of one call show as one row, as Revo shows it.

The three cases are the real ones from 22 Oct, with the times from the
server's SIP capture and call log.

Run:  python manage.py test routing.test_retry_group
"""
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from analytics.models import CallRecord
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog
from routing.retry_group import find_retry_of
from routing.signals import mirror_call_log

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
SECRET = 'test-secret'
# 22 Oct 2026, UTC. 19:26 UTC = 3:26 PM Eastern.
DAY = datetime(2026, 10, 22, tzinfo=dt_timezone.utc)


def at(h, m, s):
    whole = int(s)
    return DAY.replace(hour=h, minute=m, second=whole, microsecond=int(round((s - whole) * 1e6)))


@override_settings(CACHES=CACHE)
class Base(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.camp = Campaign.objects.create(organization=self.org, name='C-02', status='active')
        self.n = 0

    def try_(self, caller, tfn, start, end, status='no_answer', answered=None):
        self.n += 1
        c = CallLog.objects.create(
            organization=self.org, campaign=self.camp, caller_number=caller, called_number=tfn,
            twilio_call_sid=f'sid-{self.n}', status=status, ended_at=end, answered_at=answered,
            destination_number='+18779641512')
        CallLog.objects.filter(pk=c.pk).update(created_at=start)
        c.refresh_from_db()
        return c

    def group(self, date='2026-10-22'):
        out = StringIO()
        call_command('group_call_retries', date=date, apply=True, stdout=out)
        return out.getvalue()

    def shown(self, caller):
        return list(CallLog.objects.filter(caller_number=caller, retry_hidden=False).order_by('created_at'))


class RealCasesTests(Base):
    def test_12052619645_three_tries_then_a_new_call_is_two_rows(self):
        a1 = self.try_('+12052619645', '+18778186159', at(19, 26, 0.3), at(19, 26, 24.2))
        self.try_('+12052619645', '+18778186159', at(19, 26, 25.2), at(19, 26, 49.2))
        a3 = self.try_('+12052619645', '+18778186159', at(19, 26, 49.9), at(19, 26, 52.6))
        b = self.try_('+12052619645', '+18778186159', at(19, 27, 10.4), at(19, 28, 4.9),
                      status='completed', answered=at(19, 27, 22.9))
        self.group()
        self.assertEqual(self.shown('+12052619645'), [a3, b])
        a3.refresh_from_db()
        self.assertEqual(a3.retry_root_id, a1.pk)
        b.refresh_from_db()
        self.assertIsNone(b.retry_root_id)

    def test_17198591047_sixteen_tries_are_three_rows(self):
        caller, tfn = '+17198591047', '+18777425517'
        # 3:42 PM: six tries of about 7 seconds; the caller hangs up on the last.
        for s, e in [((42, 37), (42, 48)), ((42, 49), (42, 57)), ((42, 58), (43, 7)),
                     ((43, 8), (43, 15)), ((43, 16), (43, 23)), ((43, 24), (43, 31))]:
            last1 = self.try_(caller, tfn, at(19, *s), at(19, *e))
        # 4:22 PM: eight tries, the last cut at once by the caller hanging up...
        for s, e in [((22, 31), (22, 38)), ((22, 39), (22, 46)), ((22, 47), (22, 55)),
                     ((22, 56), (23, 3)), ((23, 4), (23, 10)), ((23, 11), (23, 18)),
                     ((23, 19), (23, 26))]:
            self.try_(caller, tfn, at(20, *s), at(20, *e))
        last2 = self.try_(caller, tfn, at(20, 23, 27), at(20, 23, 27.5))
        # ...who rings straight back: a new call, retried once and now live.
        self.try_(caller, tfn, at(20, 23, 30), at(20, 23, 38))
        live = self.try_(caller, tfn, at(20, 23, 39), None, status='in_progress')
        self.group()
        self.assertEqual(self.shown(caller), [last1, last2, live])

    def test_15026447543_four_tries_are_two_calls(self):
        caller, tfn = '+15026447543', '+18778186159'
        self.try_(caller, tfn, at(19, 25, 13), at(19, 25, 37))
        self.try_(caller, tfn, at(19, 25, 39), at(19, 26, 2))
        third = self.try_(caller, tfn, at(19, 26, 4), at(19, 26, 10))
        fourth = self.try_(caller, tfn, at(19, 26, 13), at(19, 26, 36))
        self.group()
        self.assertEqual(self.shown(caller), [third, fourth])

    def test_dry_run_changes_nothing(self):
        self.try_('+12052619645', '+18778186159', at(19, 26, 0.3), at(19, 26, 24.2))
        self.try_('+12052619645', '+18778186159', at(19, 26, 25.2), at(19, 26, 49.2))
        out = StringIO()
        call_command('group_call_retries', date='2026-10-22', stdout=out)
        self.assertIn('rows shown: 2 now -> 1 after grouping', out.getvalue())
        self.assertFalse(CallLog.objects.filter(retry_hidden=True).exists())


class NotARetryTests(Base):
    def test_other_number_answered_call_long_gap_or_short_try(self):
        # Another tracking number.
        self.try_('+12025550001', '+18770000001', at(19, 0, 0), at(19, 0, 24))
        self.try_('+12025550001', '+18770000002', at(19, 0, 25), at(19, 0, 49))
        # The previous call was answered.
        self.try_('+12025550002', '+18770000001', at(19, 1, 0), at(19, 1, 40),
                  status='completed', answered=at(19, 1, 10))
        self.try_('+12025550002', '+18770000001', at(19, 1, 41), at(19, 2, 5))
        # Ten seconds after the previous one ended: the caller dialled again.
        self.try_('+12025550003', '+18770000001', at(19, 2, 0), at(19, 2, 24))
        self.try_('+12025550003', '+18770000001', at(19, 2, 34), at(19, 2, 58))
        # The previous try lasted 2 seconds: the caller hung up.
        self.try_('+12025550004', '+18770000001', at(19, 3, 0), at(19, 3, 2))
        self.try_('+12025550004', '+18770000001', at(19, 3, 3), at(19, 3, 27))
        self.group()
        self.assertFalse(CallLog.objects.filter(retry_hidden=True).exists())


class MirrorTests(Base):
    def test_the_reports_show_one_row_dated_from_the_first_try(self):
        caller, tfn = '+12052619645', '+18778186159'
        a1 = self.try_(caller, tfn, at(19, 26, 0.3), at(19, 26, 24.2))
        a2 = self.try_(caller, tfn, at(19, 26, 25.2), at(19, 26, 49.2))
        a3 = self.try_(caller, tfn, at(19, 26, 49.9), at(19, 26, 52.6))
        for c in (a1, a2, a3):
            mirror_call_log(c.pk)
        self.assertEqual(CallRecord.objects.count(), 3)
        self.group()
        [row] = CallRecord.objects.all()
        self.assertEqual(row.id, a3.pk)
        self.assertEqual(row.started_at, a1.created_at)
        self.assertEqual(row.ring_seconds, 52)
        self.assertEqual(row.status, 'no_answer')

    def test_a_hidden_try_never_comes_back_into_the_reports(self):
        caller, tfn = '+12052619645', '+18778186159'
        a1 = self.try_(caller, tfn, at(19, 26, 0.3), at(19, 26, 24.2))
        self.try_(caller, tfn, at(19, 26, 25.2), at(19, 26, 49.2))
        self.group()
        a1.refresh_from_db()
        a1.save()  # e.g. the carrier lookup saving it again
        self.assertFalse(mirror_call_log(a1.pk))
        self.assertFalse(CallRecord.objects.filter(id=a1.pk).exists())

    def test_an_answered_retry_is_one_completed_row(self):
        caller, tfn = '+16465741988', '+18778186159'
        a1 = self.try_(caller, tfn, at(19, 0, 0), at(19, 0, 24))
        a2 = self.try_(caller, tfn, at(19, 0, 25), at(19, 1, 30), status='completed',
                       answered=at(19, 0, 35))
        self.group()
        mirror_call_log(a2.pk)
        [row] = CallRecord.objects.all()
        self.assertEqual((row.id, row.status, row.started_at, row.ring_seconds),
                         (a2.pk, 'completed', a1.created_at, 35))

    def test_the_call_logs_page_lists_one_row(self):
        from accounts.models import User
        from routing.services import RoutingService
        caller, tfn = '+12052619645', '+18778186159'
        self.try_(caller, tfn, at(19, 26, 0.3), at(19, 26, 24.2))
        a2 = self.try_(caller, tfn, at(19, 26, 25.2), at(19, 26, 49.2))
        self.group()
        user = User.objects.create(username='u', email='u@x.io', organization=self.org,
                                   role='admin', is_superuser=True)
        self.assertEqual([c.pk for c in RoutingService.list_calls(user)], [a2.pk])


@override_settings(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, CACHES=CACHE)
class LiveGroupingTests(Base):
    OK = {'destination': '+18779641512', 'rule': None, 'error': None, 'buyer': None}

    def setUp(self):
        super().setUp()
        PhoneNumber.objects.create(organization=self.org, number='+18778186159', vendor='Other',
                                   campaign=self.camp)

    def ring(self, sid):
        with mock.patch('tasks.enrich_call_carrier.delay'), \
                mock.patch.object(RoutingEngine, 'route_call', return_value=self.OK):
            r = Client().post('/api/twilio/asterisk/route/',
                              data=json.dumps({'caller_number': '+12052619645',
                                               'called_number': '+18778186159',
                                               'asterisk_call_id': sid}),
                              content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['action'], 'dial')
        return CallLog.objects.get(twilio_call_sid=sid)

    def previous(self, length=23, ended_ago=1.0, status='no_answer'):
        now = timezone.now()
        prev = self.try_('+12052619645', '+18778186159', now - timedelta(seconds=length + ended_ago),
                         now - timedelta(seconds=ended_ago), status=status)
        mirror_call_log(prev.pk)
        return prev

    def test_a_retry_arriving_now_takes_over_the_row(self):
        prev = self.previous()
        new = self.ring('retry-1')
        prev.refresh_from_db()
        self.assertTrue(prev.retry_hidden)
        self.assertEqual(new.retry_root_id, prev.pk)
        self.assertFalse(CallRecord.objects.filter(id=prev.pk).exists())

    def test_the_second_retry_keeps_the_first_try_as_root(self):
        first = self.previous(length=23, ended_ago=26)
        CallLog.objects.filter(pk=first.pk).update(retry_hidden=True)
        now = timezone.now()
        second = self.try_('+12052619645', '+18778186159', now - timedelta(seconds=24),
                           now - timedelta(seconds=1))
        CallLog.objects.filter(pk=second.pk).update(retry_root_id=first.pk)
        third = self.ring('retry-2')
        self.assertEqual(third.retry_root_id, first.pk)

    def test_not_a_retry_when_the_previous_call_was_answered_or_long_gone(self):
        self.previous(status='completed')
        self.assertIsNone(self.ring('new-1').retry_root_id)
        CallLog.objects.all().delete()
        self.previous(ended_ago=10)
        self.assertIsNone(self.ring('new-2').retry_root_id)

    def test_a_failing_check_never_stops_the_call(self):
        self.previous()
        with mock.patch('routing.retry_group.find_retry_of', side_effect=RuntimeError('db')):
            new = self.ring('safe-1')
        self.assertIsNone(new.retry_root_id)

    def test_find_retry_of_ignores_other_workspaces(self):
        other = Organization.objects.create(name='Other', slug='other')
        self.previous()
        self.assertIsNone(find_retry_of(other.pk, '+12052619645', '+18778186159'))
