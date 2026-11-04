"""CH-227: a burst of instantly refused tries shows as one call.

17192334345, 23 Oct: the PDSO line refused 24 tries in a row within a second
each and the sender redialled every ~1.5 s - 24 red rows.

Run:  python manage.py test routing.test_retry_refused
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from routing.models import CallLog
from routing.retry_group import find_retry_of
from routing.test_call_cleanup import Base


class RefusedBurstTests(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch('tasks.retire_used_number')
        p.start()
        self.addCleanup(p.stop)
        self.t0 = timezone.now() - timedelta(minutes=30)

    def try_at(self, sid, start, length, reason, caller='+17192334345'):
        row = self.call(sid, caller=caller, status=CallLog.Status.NO_ANSWER)
        created = self.t0 + timedelta(seconds=start)
        CallLog.objects.filter(pk=row.pk).update(
            created_at=created, ended_at=created + timedelta(seconds=length),
            block_reason=reason, ring_seconds=int(length))
        row.refresh_from_db()
        return row

    def group(self):
        call_command('group_call_retries', '--apply', stdout=StringIO())

    def shown(self):
        return list(CallLog.objects.filter(retry_hidden=False).order_by('created_at'))

    def test_instant_refusals_every_1_5_seconds_become_one_row(self):
        rows = [self.try_at(str(i), i * 1.5, 0.6, 'No Answer') for i in range(24)]
        self.group()
        shown = self.shown()
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0].pk, rows[-1].pk)

    def test_the_17192334345_afternoon_reads_two_rows_not_26(self):
        # Rang out 6-8 s, the sender cut one at 5 s, then rang out twice more,
        # then 24 instant refusals - the shape of the real call log.
        t = 0.0
        for i, length in enumerate([7, 8, 7, 7, 8, 9]):
            self.try_at(f'a{i}', t, length, 'No Answer')
            t += length + 1.5
        self.try_at('a6', t, 5, 'Not Connected')
        t += 5 + 1.5
        for i, length in enumerate([6, 7]):
            self.try_at(f'b{i}', t, length, 'No Answer')
            t += length + 1.5
        for i in range(25):
            self.try_at(f'c{i}', t, 0.6, 'No Answer')
            t += 1.5
        self.group()
        self.assertEqual(len(self.shown()), 2)

    def test_a_caller_who_hangs_up_quickly_and_calls_back_is_still_two_calls(self):
        self.try_at('a', 0, 2, 'Not Connected')
        self.try_at('b', 3, 20, 'No Answer')
        self.group()
        self.assertEqual(len(self.shown()), 2)

    def test_a_refusal_followed_much_later_is_a_new_call(self):
        self.try_at('a', 0, 0.5, 'No Answer')
        self.try_at('b', 30, 0.5, 'No Answer')
        self.group()
        self.assertEqual(len(self.shown()), 2)

    def test_a_new_try_links_live_after_an_instant_refusal(self):
        prev = self.try_at('a', 0, 0.6, 'No Answer')
        arrived = prev.ended_at + timedelta(seconds=0.9)
        found = find_retry_of(self.org.id, prev.caller_number, prev.called_number, arrived)
        self.assertEqual(found.pk, prev.pk)


class OurRefusalBurstTests(Base):
    """CH-522: a call we refused before any line rang, redialled by the sender
    within seconds, is one row - 4 Nov test, 18622022793: 7 rows in 11 s."""
    def setUp(self):
        Base.setUp(self)
        p = mock.patch('tasks.retire_used_number')
        p.start()
        self.addCleanup(p.stop)
        self.t0 = timezone.now() - timedelta(minutes=30)

    try_at = RefusedBurstTests.try_at
    group = RefusedBurstTests.group
    shown = RefusedBurstTests.shown

    def test_the_4_nov_duplicate_burst_is_one_row(self):
        # 04:09:25, :26, :30, :31, :32, :35, :36 AM - each refused at once.
        rows = [self.try_at(f'd{i}', s, 0.2, 'Duplicate call', caller='+18622022793')
                for i, s in enumerate((0, 1, 5, 6, 7, 10, 11))]
        self.group()
        shown = self.shown()
        self.assertEqual([r.pk for r in shown], [rows[-1].pk])
        self.assertEqual(CallLog.objects.filter(retry_hidden=True).count(), 6)

    def test_the_5_second_pause_in_a_burst_stays_in_it(self):
        # 16162137946, 4 Nov: 04:09:03, :05, :10, :12, :13.
        for i, s in enumerate((0, 2, 7, 9, 10)):
            self.try_at(f'p{i}', s, 0.2, 'Duplicate call', caller='+16162137946')
        self.group()
        self.assertEqual(len(self.shown()), 1)

    def test_a_no_answer_try_keeps_the_4_second_gap(self):
        self.try_at('a', 0, 0.5, 'No Answer')
        self.try_at('b', 5.5, 0.5, 'No Answer')
        self.group()
        self.assertEqual(len(self.shown()), 2)

    def test_all_lines_off_burst_is_one_row(self):
        for i, s in enumerate((0, 2, 5, 7, 8, 10)):
            self.try_at(f'n{i}', s, 0.2, 'No Destination', caller='+19362080964')
        self.group()
        self.assertEqual(len(self.shown()), 1)

    def test_a_refused_caller_calling_back_later_is_two_calls(self):
        self.try_at('x', 0, 0.2, 'No Destination')
        self.try_at('y', 30, 0.2, 'No Destination')
        self.group()
        self.assertEqual(len(self.shown()), 2)

    def test_a_new_call_links_live_after_our_refusal(self):
        prev = self.try_at('p', 0, 0.2, 'Duplicate call')
        found = find_retry_of(prev.organization_id, prev.caller_number, prev.called_number,
                              arrived_at=prev.ended_at + timedelta(seconds=1))
        self.assertEqual(found.pk, prev.pk)

    def test_nothing_is_deleted(self):
        for i in range(5):
            self.try_at(f'k{i}', i, 0.2, 'No Destination')
        self.group()
        self.assertEqual(CallLog.objects.count(), 5)
