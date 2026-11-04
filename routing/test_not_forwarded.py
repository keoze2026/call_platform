"""CH-519: only a line that actually got the call is written on it.

Sir, 4 Nov: test calls made at 4 AM EST with every destination off showed 77 of
103 under Q04|5984|, YNA, PDSO and BUB|2828| instead of "Not forwarded". Server
A's CDR had no outbound leg for any of them: nothing was dialled. Routing wrote
the first refused line's TFN and buyer on each refused call.

  - All lines off: every call is Not forwarded (no line, no buyer).
  - One line on: only that line gets calls.
  - Failover never dials a line that is off.
  - The 15-minute skip is off: failover plus an alert only.
  - clear_refused_lines puts past refused calls right, and deletes nothing.

Run:  python manage.py test routing.test_not_forwarded
"""
import io
import json
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination

SECRET = 'test-secret'
TFN = '+18888910343'
LINES = (('Q04', '+18443125984'), ('YNA', '+18667405262'), ('PDSO', '+18667404516'), ('BUB', '+18665712828'))
ON = dict(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, FAILOVER_RING_SECONDS=15,
          LINE_COOLDOWN_MISSES=5, LINE_COOLDOWN_MINUTES=15,
          CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})


@override_settings(**ON)
class NotForwardedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='Test', status='active')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                          name='R', rule_type='priority', priority=1)
        self.buyers, self.dests = {}, {}
        for i, (name, n) in enumerate(LINES, start=1):
            b = Buyer.objects.create(organization=self.org, name=name)
            self.buyers[name] = b
            self.dests[name] = Destination.objects.create(organization=self.org, buyer=b, name=name,
                                                          tfn=n, enabled=False)
            RuleDestination.objects.create(rule=rule, destination=n, buyer=b, priority=i)
        p = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def post(self, path, body):
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'):
            r = Client().post(path, data=json.dumps(body), content_type='application/json',
                              HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def dial(self):
        self.n += 1
        return self.post('/api/twilio/asterisk/route/', {
            'caller_number': f'+1555000{self.n:04d}', 'called_number': TFN,
            'asterisk_call_id': f'sid-{self.n}'})

    def next(self, out, status='NOANSWER'):
        return self.post('/api/twilio/asterisk/route-next/', {
            'call_log_id': out['call_log_id'], 'dial_status': status, 'dialed_seconds': 15})

    def switch(self, name, on):
        Destination.objects.filter(pk=self.dests[name].pk).update(enabled=on)
        cache.clear()

    def test_all_lines_off_every_call_is_not_forwarded(self):
        outs = [self.dial() for _ in range(10)]
        self.assertTrue(all(o['action'] != 'dial' for o in outs), outs)
        rows = CallLog.objects.filter(retry_hidden=False)
        self.assertEqual(rows.count(), 10)
        self.assertEqual(set(rows.values_list('destination_number', 'buyer', 'block_reason')),
                         {('', None, 'No Destination')})
        # Which line refused it, and why, is still on the call.
        trace = rows.first().routing_trace
        self.assertIn('rejected_destinations', trace)
        self.assertTrue(trace['rejected_destinations'])

    def test_one_line_on_only_that_line_gets_calls(self):
        self.switch('YNA', True)
        outs = [self.dial() for _ in range(5)]
        self.assertEqual({o.get('buyer_number') for o in outs}, {'+18667405262'})
        rows = CallLog.objects.filter(retry_hidden=False)
        self.assertEqual(set(rows.values_list('destination_number', flat=True)), {'+18667405262'})
        self.assertEqual(set(rows.values_list('buyer__name', flat=True)), {'YNA'})

    def test_failover_never_dials_a_line_that_is_off(self):
        self.switch('Q04', True)
        self.switch('PDSO', True)             # YNA (priority 2) and BUB stay off
        first = self.dial()
        self.assertEqual(first['buyer_number'], '+18443125984')
        nxt = self.next(first)
        self.assertEqual(nxt['buyer_number'], '+18667404516')
        self.assertEqual(self.next(nxt)['action'], 'hangup')
        dialled = set(CallLog.objects.values_list('destination_number', flat=True))
        self.assertEqual(dialled, {'+18443125984', '+18667404516'})

    def miss(self, name, n):
        when = timezone.now() - timedelta(minutes=1)
        number = dict(LINES)[name]
        for i in range(n):
            c = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyers[name],
                                       caller_number=f'+1666000{i:04d}', called_number=TFN,
                                       destination_number=number, twilio_call_sid=f'old-{i}',
                                       status='no_answer', block_reason='No Answer', ended_at=when)
            CallLog.objects.filter(pk=c.pk).update(created_at=when - timedelta(seconds=20))

    def test_a_line_missing_in_a_row_is_not_skipped(self):
        self.switch('Q04', True)
        self.switch('YNA', True)
        self.miss('Q04', 6)
        self.assertEqual(self.dial()['buyer_number'], '+18443125984')

    def test_a_line_missing_in_a_row_raises_an_alert_once(self):
        from notifications.detectors import check_line_misses
        self.miss('Q04', 5)
        events = check_line_misses(self.org)
        self.assertEqual(len(events), 1)
        event, data = events[0]
        self.assertEqual((event, data['type'], data['destination_number'], data['missed']),
                         ('buyer.missed', 'destination', '+18443125984', 5))
        self.assertEqual(check_line_misses(self.org), [])          # once a day per line

    def test_four_misses_or_an_answer_raise_no_alert(self):
        from notifications.detectors import check_line_misses
        self.miss('Q04', 4)
        self.assertEqual(check_line_misses(self.org), [])

    @override_settings(FAILOVER_RING_SECONDS=0)
    def test_no_alert_with_failover_off(self):
        from notifications.detectors import check_line_misses
        self.miss('Q04', 5)
        self.assertEqual(check_line_misses(self.org), [])


class ClearRefusedLinesTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='Test', status='active')
        self.q04 = Buyer.objects.create(organization=self.org, name='Q04')
        self.n = 0

    def row(self, **kw):
        self.n += 1
        fields = dict(organization=self.org, campaign=self.campaign, caller_number=f'+1555{self.n:07d}',
                      called_number=TFN, twilio_call_sid=f's{self.n}', status='no_answer',
                      block_reason='No Destination', buyer=self.q04, destination_number='+18443125984',
                      ended_at=timezone.now())
        fields.update(kw)
        return CallLog.objects.create(**fields)

    def run_cmd(self, *extra):
        out = io.StringIO()
        day = timezone.localtime(timezone.now(), timezone=__import__('zoneinfo').ZoneInfo('America/New_York'))
        call_command('clear_refused_lines', '--start', f'{day:%Y-%m-%d}', '--end', f'{day:%Y-%m-%d}',
                     *extra, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        r = self.row()
        out = self.run_cmd()
        self.assertIn('1 to clear', out)
        r.refresh_from_db()
        self.assertEqual((r.destination_number, r.buyer_id), ('+18443125984', self.q04.id))

    def test_apply_clears_refused_rows_and_keeps_what_they_said(self):
        refused = self.row()
        dialled = self.row(block_reason='No Answer', ring_seconds=15)       # really rang the line
        answered = self.row(status='completed', block_reason='', duration=60)
        with_money = self.row(revenue=5)
        self.run_cmd('--apply')
        refused.refresh_from_db()
        self.assertEqual((refused.destination_number, refused.buyer_id), ('', None))
        self.assertEqual(refused.routing_trace['ch519_cleared']['destination_number'], '+18443125984')
        self.assertEqual(refused.routing_trace['ch519_cleared']['buyer_name'], 'Q04')
        for other in (dialled, answered, with_money):
            other.refresh_from_db()
            self.assertEqual((other.destination_number, other.buyer_id), ('+18443125984', self.q04.id))
        self.assertEqual(CallLog.objects.count(), 4)                         # nothing deleted
