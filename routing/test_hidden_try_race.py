"""CH-223: a hidden try never comes back into Reports.

Run:  python manage.py test routing.test_hidden_try_race
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from analytics.models import CallRecord
from campaigns.models import Campaign
from routing.models import CallLog
from routing.signals import mirror_call_log

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=CACHE)
class HiddenTryRaceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Org', slug='org')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-03')

    def try_row(self, sid, hidden=False):
        now = timezone.now()
        return CallLog.objects.create(
            organization=self.org, campaign=self.campaign, caller_number='+13072281061',
            called_number='+18888910343', destination_number='+18667404516',
            twilio_call_sid=sid, status=CallLog.Status.NO_ANSWER, block_reason='Not Connected',
            ended_at=now, retry_hidden=hidden)

    def test_a_try_hidden_while_being_copied_does_not_stay(self):
        row = self.try_row('a')

        def hide_meanwhile(call):
            # The retry arrives and hides this try between the mirror's read
            # and its write - the 13072281061 race.
            CallLog.objects.filter(pk=call.pk).update(retry_hidden=True)
            CallRecord.objects.filter(id=call.pk).delete()
            return call.created_at, 44

        with mock.patch('routing.retry_group.group_view', side_effect=hide_meanwhile):
            self.assertFalse(mirror_call_log(row.id))
        self.assertFalse(CallRecord.objects.filter(id=row.id).exists())

    def test_a_visible_try_is_still_copied(self):
        row = self.try_row('a')
        self.assertTrue(mirror_call_log(row.id))
        self.assertTrue(CallRecord.objects.filter(id=row.id).exists())

    def test_cleanup_removes_only_hidden_copies(self):
        hidden = self.try_row('a')
        shown = self.try_row('b')
        mirror_call_log(hidden.id)
        mirror_call_log(shown.id)
        CallLog.objects.filter(pk=hidden.pk).update(retry_hidden=True)
        out = StringIO()
        call_command('remove_hidden_tries', stdout=out)
        self.assertIn('1 hidden tries still showing', out.getvalue())
        self.assertTrue(CallRecord.objects.filter(id=hidden.id).exists())
        call_command('remove_hidden_tries', '--apply', stdout=StringIO())
        self.assertFalse(CallRecord.objects.filter(id=hidden.id).exists())
        self.assertTrue(CallRecord.objects.filter(id=shown.id).exists())
        self.assertTrue(CallLog.objects.filter(id=hidden.id).exists())
