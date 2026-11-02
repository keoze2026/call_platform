"""CH-464: Server A routes on its own while Server B is down (Sir, 2 Nov).

Each test sets up a campaign the usual way, asks the live routing what it
would do, builds the plan B sends to A, and checks that A - with B away -
decides the same: priority and weight, lines switched off, buyer and line
caps, buyer, line and campaign concurrency, blocked and allowed callers,
duplicates, balance. Then the calls A routed are sent to B, written with A's
call ids, and their hang-up reports bill them as usual.

Run:  python manage.py test routing.test_fallback
"""
import importlib
import json
import os
import random
import shutil
import sys
import tempfile
import uuid
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer, BuyerCap
from campaigns.models import Campaign, CampaignCap
from phone_numbers.models import PhoneNumber
from routing.engine import RoutingEngine
from routing.fallback import build_plan
from routing.models import CallLog, RoutingRule, RuleDestination
from spam_protection.models import Blacklist, Whitelist

SECRET = 'test-secret'
TFN = '+18888910343'  # as the AGI sends it: + and the dialled digits
A, B, C = '+18443122521', '+18332779804', '+18443125984'

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'fallback')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
fb = importlib.import_module('avx_fallback')
sync = importlib.import_module('fallback_sync')
agi = importlib.import_module('fallback_agi')


@override_settings(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class FallbackBase(TestCase):
    def setUp(self):
        cache.clear()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        for name, value in (('STATE_DIR', self.dir),
                            ('PLAN_DB', os.path.join(self.dir, 'plan.sqlite3')),
                            ('JOURNAL_DB', os.path.join(self.dir, 'journal.sqlite3')),
                            ('PENDING_DIR', os.path.join(self.dir, 'pending'))):
            p = mock.patch.object(fb, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-02', duplicate_handling='normal')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, status='active')
        self.rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign,
                                               name='R', rule_type='priority', priority=1)
        self.buyers = {}
        self.balance = mock.patch.object(RoutingEngine, 'check_balance', return_value=True)
        self.balance.start()
        self.addCleanup(self.balance.stop)
        self.n = 0
        self.live_ids = set()

    def line(self, number, priority=1, weight=100, buyer='Q04', cap=0, **caps):
        b = self.buyers.get(buyer) or Buyer.objects.create(organization=self.org, name=buyer)
        self.buyers[buyer] = b
        Destination.objects.create(organization=self.org, buyer=b, name=f'{buyer}|{number[-4:]}',
                                   tfn=number, enabled=True, concurrency_cap=cap, **caps)
        return RuleDestination.objects.create(rule=self.rule, destination=number, buyer=b,
                                              priority=priority, weight=weight)

    # B, as it routes today
    def b_routes(self, caller=None):
        self.n += 1
        caller = caller or f'+1555{self.n:07d}'
        d = RoutingEngine.route_call(str(self.campaign.id), {'caller_number': caller,
                                                              'twilio_call_sid': f'b{self.n}'})
        return d.get('destination') or d.get('error')

    # A, with B away
    def sync_plan(self):
        fb.write_plan(json.loads(json.dumps(build_plan())))

    def a_routes(self, caller=None, uniqueid=None, live=None, now=None, rng=None):
        self.n += 1
        caller = caller or f'+1555{self.n:07d}'
        uniqueid = uniqueid or f'1793.{self.n}'
        reply = fb.decide(TFN, caller, uniqueid, now=now,
                          live=live if live is not None else (self.live_ids, set()), rng=rng)
        return reply

    def a_dest(self, **kw):
        r = self.a_routes(**kw)
        return r.get('buyer_number') or r.get('reason')


class SameDecisionTests(FallbackBase):
    def test_priority_then_weight(self):
        self.line(B, priority=2)
        self.line(A, priority=1)
        self.sync_plan()
        self.assertEqual(self.b_routes(), A)
        self.assertEqual({self.a_dest() for _ in range(10)}, {A})

    def test_weight_split(self):
        self.line(A, weight=75)
        self.line(B, weight=25)
        self.sync_plan()
        got = [self.a_dest(rng=random.Random(i)) for i in range(400)]
        self.assertTrue(250 <= got.count(A) <= 350, got.count(A))

    def test_line_switched_off(self):
        self.line(A, priority=1)
        self.line(B, priority=2)
        Destination.objects.filter(tfn=A).update(enabled=False)
        self.sync_plan()
        self.assertEqual(self.b_routes(), B)
        self.assertEqual(self.a_dest(), B)

    def test_buyer_paused(self):
        self.line(A, buyer='Q04')
        Buyer.objects.filter(name='Q04').update(status='paused')
        self.sync_plan()
        self.assertEqual(self.b_routes(), 'Destination missed (buyer paused)')
        self.assertEqual(self.a_dest(), 'Destination missed (buyer paused)')

    def test_buyer_daily_cap_from_b_counts(self):
        self.line(A, buyer='Q04')
        self.line(B, buyer='R48', priority=2)
        q04 = self.buyers['Q04']
        BuyerCap.objects.create(buyer=q04, max_calls_daily=2)
        for i in range(2):
            CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=q04,
                                   caller_number=f'+1555000{i}', called_number=TFN, destination_number=A,
                                   twilio_call_sid=f'old{i}', status='completed')
        self.sync_plan()
        self.assertEqual(self.b_routes(), B)
        self.assertEqual(self.a_dest(), B)

    def test_buyer_cap_counts_calls_a_sent_meanwhile(self):
        self.line(A, buyer='Q04')
        self.line(B, buyer='R48', priority=2)
        BuyerCap.objects.create(buyer=self.buyers['Q04'], max_calls_daily=3)
        CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyers['Q04'],
                               caller_number='+15550000', called_number=TFN, destination_number=A,
                               twilio_call_sid='old', status='completed')
        self.sync_plan()
        self.assertEqual([self.a_dest() for _ in range(4)], [A, A, B, B])

    def test_line_daily_cap(self):
        self.line(A, daily_cap=2)
        self.line(B, priority=2)
        self.sync_plan()
        self.assertEqual([self.a_dest() for _ in range(3)], [A, A, B])

    def test_line_concurrency(self):
        self.line(A, cap=1)
        self.line(B, priority=2)
        self.sync_plan()
        first = self.a_routes(uniqueid='live-1')
        self.assertEqual(first['buyer_number'], A)
        self.live_ids.add('live-1')                       # still talking
        self.assertEqual(self.a_dest(), B)
        self.live_ids.clear()                              # hung up
        self.assertEqual(self.a_dest(), A)

    def test_calls_b_routed_count_as_live(self):
        self.line(A, cap=1)
        self.line(B, priority=2)
        self.sync_plan()
        fb.note_b_call(TFN, '+15551112222', 'b-live',
                       {'action': 'dial', 'buyer_number': A, 'call_log_id': str(uuid.uuid4())})
        self.live_ids.add('b-live')
        self.assertEqual(self.a_dest(), B)

    def test_buyer_concurrency(self):
        self.line(A, buyer='Q04')
        self.line(C, buyer='Q04')
        self.line(B, buyer='R48', priority=2)
        Buyer.objects.filter(name='Q04').update(max_concurrency=2)
        self.sync_plan()
        for i in range(2):
            r = self.a_routes(uniqueid=f'q{i}')
            self.assertIn(r['buyer_number'], (A, C))
            self.live_ids.add(f'q{i}')
        self.assertEqual(self.a_dest(), B)

    def test_campaign_concurrency(self):
        self.line(A)
        CampaignCap.objects.create(campaign=self.campaign, max_concurrency=1)
        self.sync_plan()
        self.a_routes(uniqueid='c1')
        self.live_ids.add('c1')
        self.assertEqual(self.a_dest(), 'Campaign cap reached')

    def test_campaign_daily_limit(self):
        self.line(A)
        CampaignCap.objects.create(campaign=self.campaign, max_calls_daily=1)
        CallLog.objects.create(organization=self.org, campaign=self.campaign, caller_number='+15550000',
                               called_number=TFN, destination_number=A, twilio_call_sid='old',
                               status='completed')
        self.sync_plan()
        self.assertEqual(self.b_routes(), 'Campaign daily limit reached')
        self.assertEqual(self.a_dest(), 'Campaign daily limit reached')

    def test_team_block_drops_without_a_row(self):
        self.line(A)
        Blacklist.objects.create(organization_id=self.org.id, phone_number='5557778888', reason='manual')
        self.sync_plan()
        self.assertEqual(self.a_dest(caller='+15557778888'), 'CallerId Blocked')
        self.assertEqual(fb.Journal().db.execute('SELECT COUNT(*) FROM calls').fetchone()[0], 0)

    def test_whitelist_wins_over_block(self):
        self.line(A)
        Blacklist.objects.create(organization_id=self.org.id, phone_number='5557778888', reason='manual')
        Whitelist.objects.create(organization_id=self.org.id, phone_number='+15557778888')
        self.sync_plan()
        self.assertEqual(self.a_dest(caller='+15557778888'), A)

    def test_automatic_block_is_a_refused_row(self):
        self.line(A)
        Blacklist.objects.create(organization_id=self.org.id, phone_number='15557778888', reason='spam')
        self.sync_plan()
        self.assertEqual(self.b_routes(caller='+15557778888'), 'CallerId Blocked')
        self.assertEqual(self.a_dest(caller='+15557778888'), 'CallerId Blocked')
        row = fb.Journal().db.execute('SELECT outcome, auto_block FROM calls').fetchone()
        self.assertEqual(row, ('refused', 1))

    def test_buyer_block_skips_that_buyer_only(self):
        self.line(A, buyer='Q04')
        self.line(B, buyer='R48', priority=2)
        Blacklist.objects.create(organization_id=self.org.id, phone_number='5557778888',
                                 buyer=self.buyers['Q04'], reason='manual')
        self.sync_plan()
        self.assertEqual(self.b_routes(caller='+15557778888'), B)
        self.assertEqual(self.a_dest(caller='+15557778888'), B)

    def test_different_never_the_same_tfn(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(duplicate_handling='different')
        self.line(A)
        self.line(B, buyer='R48', priority=2)
        self.sync_plan()
        caller = '+15553334444'
        self.assertEqual(self.a_dest(caller=caller), A)
        self.assertEqual(self.a_dest(caller=caller), B)
        self.assertEqual(self.a_dest(caller=caller), 'Duplicate call')

    def test_insufficient_funds(self):
        self.line(A)
        self.balance.stop()
        with mock.patch.object(RoutingEngine, 'check_balance', return_value=False):
            self.sync_plan()
        self.balance.start()
        self.assertEqual(self.a_dest(), 'Insufficient Funds')

    def test_paused_campaign_and_unknown_number(self):
        self.line(A)
        Campaign.objects.filter(pk=self.campaign.pk).update(status='paused')
        self.sync_plan()
        self.assertEqual(self.a_dest(), 'campaign_inactive')
        self.assertEqual(fb.decide('+18000000000', '+15551231234', 'x', live=(set(), set()))['reason'],
                         'number_not_found')

    def test_no_plan_yet(self):
        self.assertEqual(self.a_dest(), 'no_fallback_plan')

    def test_buyer_live_tfns_rotate(self):
        # The rule names the buyer with a number that is not one of its own
        # lines: the buyer's live TFNs take turns, as pick_live_destination.
        b = Buyer.objects.create(organization=self.org, name='Q07')
        for n in (A, B):
            Destination.objects.create(organization=self.org, buyer=b, name=n, tfn=n, enabled=True)
        RuleDestination.objects.create(rule=self.rule, destination='+18005550000', buyer=b)
        self.sync_plan()
        got = [self.a_dest() for _ in range(4)]
        self.assertEqual(sorted(set(got)), sorted({A, B}))

    def test_calls_b_has_not_got_yet_still_count_after_a_new_plan(self):
        # Review finding: A routed calls, B came back long enough to send a
        # new plan but not to take the calls, then went away again.
        self.line(A, buyer='Q04')
        self.line(B, buyer='R48', priority=2)
        BuyerCap.objects.create(buyer=self.buyers['Q04'], max_calls_daily=3)
        self.sync_plan()
        self.assertEqual([self.a_dest() for _ in range(3)], [A, A, A])
        self.sync_plan()                      # B's counts do not have them
        self.assertEqual(self.a_dest(), B)

    def test_b_routed_call_to_a_rule_number_counts_for_its_buyer(self):
        # The rule line names buyer Q07 with a number not on its Destinations
        # page; B's call there still counts against Q07's concurrency.
        q07 = Buyer.objects.create(organization=self.org, name='Q07', max_concurrency=1)
        RuleDestination.objects.create(rule=self.rule, destination='+18005550000', buyer=q07)
        self.line(B, buyer='R48', priority=2)
        self.sync_plan()
        fb.note_b_call(TFN, '+15551112222', 'b-live',
                       {'action': 'dial', 'buyer_number': '+18005550000', 'call_log_id': str(uuid.uuid4())})
        self.live_ids.add('b-live')
        self.assertEqual(self.a_dest(), B)

    def test_a_very_old_plan_is_not_used(self):
        self.line(A)
        self.sync_plan()
        later = timezone.now() + timedelta(days=3)
        self.assertEqual(self.a_dest(now=later.timestamp()), 'fallback_plan_too_old')

    def test_day_rolls_over_during_the_outage(self):
        self.line(A, daily_cap=1)
        self.line(B, priority=2)
        self.sync_plan()
        self.assertEqual(self.a_dest(), A)
        self.assertEqual(self.a_dest(), B)
        tomorrow = timezone.now() + timedelta(days=1, hours=1)
        self.assertEqual(self.a_dest(now=tomorrow.timestamp()), A)


class SyncToBTests(FallbackBase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        p = mock.patch.object(sync, 'call_b', side_effect=self._call_b)
        p.start()
        self.addCleanup(p.stop)
        for task in ('tasks.enrich_call_carrier.delay', 'tasks.charge_rejected_call.delay',
                     'tasks.mirror_call_record.delay', 'tasks.retire_used_number.delay'):
            q = mock.patch(task)
            q.start()
            self.addCleanup(q.stop)

    def _call_b(self, path, body=None, timeout=20):
        if body is None:
            r = self.client.get(path, HTTP_X_ASTERISK_SECRET=SECRET)
        else:
            r = self.client.post(path, data=json.dumps(body), content_type='application/json',
                                 HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_plan_needs_the_secret(self):
        self.assertEqual(self.client.get('/api/twilio/asterisk/fallback-plan/').status_code, 403)
        self.assertEqual(self.client.post('/api/twilio/asterisk/fallback-calls/', data='{}',
                                          content_type='application/json').status_code, 403)

    def test_sync_fetches_the_plan(self):
        self.line(A)
        self.assertTrue(sync.fetch_plan())
        self.assertEqual(self.a_dest(), A)

    def test_a_call_reaches_b_and_is_billed(self):
        self.line(A)
        self.sync_plan()
        reply = self.a_routes(caller='+15559990000', uniqueid='1793.77')
        cid = reply['call_log_id']
        self.assertTrue(os.path.exists(os.path.join(fb.PENDING_DIR, cid)))
        self.assertFalse(CallLog.objects.filter(pk=cid).exists())     # B was away

        sent = sync.push_calls(fb.Journal())
        self.assertEqual(sent, [cid])
        with mock.patch.object(sync, 'CALL_ENDED', '/nonexistent'):
            sync.release_reports(sent)
        self.assertFalse(os.path.exists(os.path.join(fb.PENDING_DIR, cid)))

        row = CallLog.objects.get(pk=cid)
        self.assertEqual((row.status, row.destination_number, row.buyer, row.twilio_call_sid),
                         ('in_progress', A, self.buyers['Q04'], '1793.77'))
        self.assertEqual(row.routing_rule, self.rule)
        self.assertIsNotNone(row.rule_destination_id)
        self.assertIn('Server A', row.routing_trace.get('routed_by', ''))

        # The hang-up report that waited on A now finds its call.
        r = self.client.post('/api/twilio/asterisk/call-ended/', data=json.dumps(
            {'call_log_id': cid, 'duration': 95, 'answered': True, 'dial_status': 'ANSWER'}),
            content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200)
        row.refresh_from_db()
        self.assertEqual((row.status, row.duration), ('completed', 95))

        # Sent again (A retrying): nothing doubles.
        self.assertEqual(self._call_b('/api/twilio/asterisk/fallback-calls/', {'calls': [
            {'id': cid, 'campaign_id': str(self.campaign.id), 'caller': '+1', 'called': TFN}]})['results'],
            {cid: 'exists'})
        self.assertEqual(CallLog.objects.count(), 1)

    def test_the_call_keeps_its_real_time(self):
        self.line(A)
        self.sync_plan()
        when = timezone.now() - timedelta(minutes=40)
        cid = self.a_routes(now=when.timestamp())['call_log_id']
        sync.push_calls(fb.Journal())
        self.assertLess(abs((CallLog.objects.get(pk=cid).created_at - when).total_seconds()), 1)

    def test_refused_calls_are_written_as_b_writes_them(self):
        self.line(A, buyer='Q04')
        Buyer.objects.filter(name='Q04').update(status='paused')
        Blacklist.objects.create(organization_id=self.org.id, phone_number='5557778888', reason='spam')
        self.sync_plan()
        self.a_routes()
        self.a_routes(caller='+15557778888')
        sync.push_calls(fb.Journal())
        missed = CallLog.objects.get(caller_number__startswith='+1555000')
        self.assertEqual((missed.status, missed.block_reason, missed.destination_number, missed.buyer),
                         ('no_answer', 'No Destination', A, self.buyers['Q04']))
        blocked = CallLog.objects.get(caller_number='+15557778888')
        self.assertEqual((blocked.status, blocked.block_reason), ('failed', 'CallerId Blocked'))

    def test_b_answered_too_late(self):
        # B wrote the call but its answer never reached Asterisk; A routed it.
        self.line(A)
        self.sync_plan()
        CallLog.objects.create(organization=self.org, campaign=self.campaign, caller_number='+15550001',
                               called_number=TFN, destination_number=A, twilio_call_sid='1793.5',
                               status='in_progress')
        cid = self.a_routes(caller='+15550001', uniqueid='1793.5')['call_log_id']
        sync.push_calls(fb.Journal())
        late = CallLog.objects.get(twilio_call_sid='1793.5')
        self.assertTrue(late.retry_hidden)
        self.assertEqual(late.status, 'failed')
        self.assertEqual(CallLog.objects.get(pk=cid).status, 'in_progress')

    def test_b_still_down_keeps_the_calls(self):
        self.line(A)
        self.sync_plan()
        self.a_routes()
        with mock.patch.object(sync, 'call_b', side_effect=OSError('no route to host')):
            self.assertEqual(sync.push_calls(fb.Journal()), [])
            self.assertFalse(sync.fetch_plan())
        self.assertEqual(fb.Journal().db.execute('SELECT COUNT(*) FROM calls WHERE synced = 0').fetchone()[0], 1)
        self.assertEqual(len(sync.push_calls(fb.Journal())), 1)


class AgiTests(FallbackBase):
    """fallback_agi.route: B first, A only when B is not there."""

    def setUp(self):
        super().setUp()
        import socket
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(50)
        self.addCleanup(srv.close)
        self.up = f'http://127.0.0.1:{srv.getsockname()[1]}'
        dead = socket.socket()
        dead.bind(('127.0.0.1', 0))
        self.down = f'http://127.0.0.1:{dead.getsockname()[1]}'
        dead.close()
        self.line(A)
        self.sync_plan()
        p = mock.patch.object(fb, 'live_channels', return_value=(set(), set()))
        p.start()
        self.addCleanup(p.stop)

    def test_b_answers_its_reply_is_used_and_noted(self):
        reply = {'action': 'dial', 'buyer_number': B, 'call_log_id': str(uuid.uuid4())}
        got = agi.route(self.up, lambda: reply, TFN, '+15551230000', 'u1')
        self.assertIs(got, reply)
        rows = fb.Journal().db.execute('SELECT source, dest, uniqueid FROM calls').fetchall()
        self.assertEqual(rows, [('b', B, 'u1')])

    def test_b_refusal_is_not_overruled(self):
        reply = {'action': 'hangup', 'reason': 'Campaign cap reached'}
        self.assertIs(agi.route(self.up, lambda: reply, TFN, '+15551230000', 'u1'), reply)

    def test_b_refusing_connections_affects_that_call_only(self):
        def never():
            raise AssertionError('B must not be asked when nothing listens')
        got = agi.route(self.down, never, TFN, '+15551230000', 'u2')
        self.assertEqual((got['action'], got['buyer_number']), ('dial', A))
        reply = {'action': 'dial', 'buyer_number': B, 'call_log_id': str(uuid.uuid4())}
        self.assertIs(agi.route(self.up, lambda: reply, TFN, '+15551230001', 'u3'), reply)

    def test_b_switched_off_is_skipped_for_a_few_seconds(self):
        import socket
        def never():
            raise AssertionError('B must not be asked')
        with mock.patch.object(agi.socket, 'create_connection', side_effect=socket.timeout()):
            self.assertEqual(agi.route(self.up, never, TFN, '+15551230000', 'u2')['buyer_number'], A)
        # Nothing asked of B for the next calls: they do not each wait.
        self.assertEqual(agi.route(self.up, never, TFN, '+15551230001', 'u3')['buyer_number'], A)
        with mock.patch.object(agi, 'DOWN_HOLD', 0):
            reply = {'action': 'hangup', 'reason': 'x'}
            self.assertIs(agi.route(self.up, lambda: reply, TFN, '+15551230002', 'u4'), reply)

    def test_b_errors_and_timeouts(self):
        import urllib.error
        for exc in (urllib.error.HTTPError('u', 502, 'bad gateway', {}, None),
                    urllib.error.URLError('timed out'), TimeoutError()):
            agi._mark_up()
            def fail(exc=exc):
                raise exc
            got = agi.route(self.up, fail, TFN, '+15551230000', 'u4')
            self.assertEqual(got['buyer_number'], A, exc)

    def test_b_saying_forbidden_is_not_an_outage(self):
        import urllib.error
        def forbidden():
            raise urllib.error.HTTPError('u', 403, 'Forbidden', {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            agi.route(self.up, forbidden, TFN, '+15551230000', 'u5')

    def test_a_broken_fallback_hangs_up_as_before(self):
        with mock.patch.object(fb, 'decide', side_effect=RuntimeError('disk full')):
            got = agi.route(self.down, lambda: None, TFN, '+15551230000', 'u6')
        self.assertEqual(got, {'action': 'hangup', 'reason': 'fallback_error'})


class RouteCallAgiTests(FallbackBase):
    """The AGI script itself, run as Asterisk runs it (stdin/stdout)."""

    def run_agi(self, base):
        import subprocess
        script = os.path.join(SCRIPTS, 'route_call.py')
        stdin = ('agi_callerid: 15551234567\nagi_extension: 18888910343\nagi_uniqueid: 1793.42\n\n'
                 + '200 result=1\n' * 30)
        secret = os.path.join(self.dir, 'secret')
        with open(secret, 'w') as f:
            f.write(SECRET + '\n')
        env = dict(os.environ, AVX_ROUTE_BASE=base, AVX_FALLBACK_DIR=self.dir,
                   AVX_FALLBACK_CODE=SCRIPTS, AVX_SECRET_FILE=secret, AVX_ROUTE_TIMEOUT='5')
        out = subprocess.run([sys.executable, script], input=stdin, capture_output=True, text=True,
                             env=env, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = {}
        for line in out.stdout.splitlines():
            if line.startswith('SET VARIABLE '):
                name, value = line[len('SET VARIABLE '):].split(' ', 1)
                got[name] = value.strip('"')
        return got, out.stdout

    def test_b_down_the_call_still_goes_out(self):
        import socket
        self.line(A)
        self.sync_plan()
        dead = socket.socket()
        dead.bind(('127.0.0.1', 0))
        base = f'http://127.0.0.1:{dead.getsockname()[1]}'
        dead.close()
        got, out = self.run_agi(base)
        self.assertEqual((got['ROUTE_ACTION'], got['BUYER_NUMBER'], got['RECORD_CALL']), ('dial', A, '1'))
        self.assertTrue(os.path.exists(os.path.join(fb.PENDING_DIR, got['CALL_LOG_ID'])))
        self.assertIn('routed by Server A', out)

    def test_b_up_its_answer_is_used_unchanged(self):
        import http.server
        import threading
        seen = {}

        class B(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.update(body, secret=self.headers.get('X-Asterisk-Secret'))
                reply = json.dumps({'action': 'dial', 'buyer_number': C, 'call_log_id': 'b-id',
                                    'max_duration': 1800, 'recording_enabled': False}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(('127.0.0.1', 0), B)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.line(A)
        self.sync_plan()
        got, _ = self.run_agi(f'http://127.0.0.1:{srv.server_address[1]}')
        self.assertEqual(got, {'ROUTE_ACTION': 'dial', 'BUYER_NUMBER': C, 'CALL_LOG_ID': 'b-id',
                               'MAX_DURATION': '1800', 'RECORD_CALL': '0'})
        self.assertEqual(seen, {'caller_number': '+15551234567', 'called_number': '+18888910343',
                                'asterisk_call_id': '1793.42', 'secret': SECRET})
        self.assertEqual(fb.Journal().db.execute('SELECT source, dest FROM calls').fetchall(), [('b', C)])
