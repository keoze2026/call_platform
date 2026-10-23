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
