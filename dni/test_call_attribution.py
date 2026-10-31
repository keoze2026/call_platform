"""CH-452: a call on a number-pool number closes the website visit and shows
its ad source on the call."""
from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from accounts.models import Organization
from analytics.services import AnalyticsService
from dni.attribution import mark_called, visit_for_call
from dni.models import DNINumber, DNIPool, DNISession
from dni.schemas import AssignNumberSchema
from dni.services import DNIService
from routing.models import CallLog


class CallAttributionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Acme', slug='acme')
        self.pool = DNIPool.objects.create(organization=self.org, name='Site pool', session_duration_minutes=30)
        self.num = DNINumber.objects.create(pool=self.pool, number='+18885550101')
        self.seq = 0

    def visit(self):
        DNIService.assign_number(AssignNumberSchema(
            pool_id=str(self.pool.id), visitor_id='v1', utm_source='google', utm_medium='cpc',
            utm_campaign='medicare', gclid='abc123', landing_page='https://example.com/a'))
        return DNISession.objects.get(visitor_id='v1')

    def call(self, org=None, called='18885550101', at=None):
        self.seq += 1
        c = CallLog.objects.create(organization=org or self.org, caller_number='+15555550123',
                                   called_number=called, twilio_call_sid='s%d' % self.seq)
        if at:
            CallLog.objects.filter(pk=c.pk).update(created_at=at)
            c.refresh_from_db()
        return c

    def test_the_call_closes_the_visit_and_frees_the_number(self):
        session = self.visit()
        self.assertEqual(DNINumber.objects.get(pk=self.num.pk).status, DNINumber.Status.IN_USE)
        call = self.call()
        self.assertTrue(mark_called(call))
        session.refresh_from_db()
        self.assertEqual(session.status, DNISession.Status.CALLED)
        self.assertEqual(DNINumber.objects.get(pk=self.num.pk).status, DNINumber.Status.AVAILABLE)
        self.assertEqual(visit_for_call(call).pk, session.pk)

    def test_the_call_shows_the_ad_source(self):
        self.visit()
        call = self.call()
        mark_called(call)
        call = CallLog.objects.select_related('campaign', 'buyer', 'publisher', 'routing_rule').get(pk=call.pk)
        events = {e['event']: e for e in AnalyticsService.format_call_detail(call)['timeline']}
        d = events['website_visit']['detail']
        self.assertEqual((d['utm_source'], d['utm_medium'], d['utm_campaign'], d['gclid']),
                         ('google', 'cpc', 'medicare', 'abc123'))
        self.assertEqual(d['pool'], 'Site pool')

    def test_other_workspace_or_after_the_visit_expired_is_not_matched(self):
        self.visit()
        other = Organization.objects.create(name='Other', slug='other')
        self.assertFalse(mark_called(self.call(org=other)))
        late = self.call(at=timezone.now() + timedelta(minutes=45))
        self.assertFalse(mark_called(late))
        self.assertIsNone(visit_for_call(late))

    def test_an_ordinary_tfn_call_is_untouched(self):
        self.assertFalse(mark_called(self.call(called='18885559999')))
