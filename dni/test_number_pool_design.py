"""CH-460 (Sir, 31 Oct): the full number pool design.

  * the website script and its public calls: number / ping / release
  * free number used longest ago, cool-down, closed-browser delay, misses
  * landing pages scanned for their phone numbers, the numbers swapped
  * spam rules on calls to pool numbers: repeat callers, call floods,
    silent/short callers, anonymous; numbers pulled and restored
  * the pool page figures; advertisers only

Run:  python manage.py test dni.test_number_pool_design
"""
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from dni.models import DNICall, DNILander, DNINumber, DNIPool, DNISession
from dni.scanner import find_numbers
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher
from routing.engine import RoutingEngine
from routing.models import CallLog, RoutingRule, RuleDestination
from spam_protection.models import Blacklist

SECRET = 'test-secret'
LINE = '+18667404516'

PAGE = """<html><head><script>var x='1-800-555-9999'</script></head><body>
<header><a class="btn-call" href="tel:+18005550000">Call 1-800-555-0000</a></header>
<section class="hero"><h1>Call (800) 555-0000 now</h1></section>
<footer>Questions? 888.555.1212</footer></body></html>"""


@override_settings(ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET, ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CORS_ALLOWED_ORIGINS=['https://app.avortyx.io'])
class NumberPoolDesignTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Acme', slug='acme')
        admin = User.objects.create(username='ad', email='ad@x.io', organization=self.org, role='admin', is_active=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        self.campaign = Campaign.objects.create(organization=self.org, name='Medicare', revenue_amount=Decimal('1'),
                                                payout_amount=Decimal('1'), min_call_duration=0,
                                                duplicate_handling='normal')
        self.tfns = [PhoneNumber.objects.create(organization=self.org, number=f'+1888555010{i}', vendor='Other',
                                                campaign=self.campaign, status='active') for i in range(1, 4)]
        buyer = Buyer.objects.create(organization=self.org, name='B1')
        Destination.objects.create(organization=self.org, buyer=buyer, name='B1|4516', tfn=LINE, enabled=True)
        rule = RoutingRule.objects.create(organization=self.org, campaign=self.campaign, name='R',
                                          rule_type='priority', priority=1)
        RuleDestination.objects.create(rule=rule, destination=LINE, buyer=buyer, priority=1)
        for target in (mock.patch.object(RoutingEngine, 'check_balance', return_value=True),
                       mock.patch('celery.app.task.Task.apply_async')):
            target.start()
            self.addCleanup(target.stop)
        self.n = 0

    # ── helpers ──────────────────────────────────────────────────────────────
    def pool(self, size=3, **kw):
        body = {'name': 'Bing lander', 'fallback_number': '+18005550000', 'campaign_id': str(self.campaign.id),
                'attached_number_ids': [str(t.id) for t in self.tfns[:size]], **kw}
        r = self.api.post('/api/dni/pools', body, content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def number(self, p, vid, q='?utm_source=bing&utm_term=medicare&gclid=G1&sub_id=s7', origin='https://lp.example.com'):
        return Client().get('/api/dni/v1/number', {'pool': p['public_key'], 'vid': vid, 'q': q,
                                                   'url': 'https://lp.example.com/offer', 'ref': ''},
                            HTTP_ORIGIN=origin)

    def call(self, called, caller=None, seconds=30):
        """A call through our phone system: route, then hang-up."""
        self.n += 1
        caller = caller or f'+1555000{self.n:04d}'
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'), \
                mock.patch('tasks.retire_used_number.delay'), self.captureOnCommitCallbacks(execute=True):
            r = Client().post('/api/twilio/asterisk/route/', data=json.dumps(
                {'caller_number': caller, 'called_number': called, 'asterisk_call_id': f'sid-{self.n}'}),
                content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET).json()
            row = CallLog.objects.filter(twilio_call_sid=f'sid-{self.n}').first()
            if row is not None:
                pc = DNICall.objects.filter(call_log=row).first()
                if pc:  # the call lasted `seconds`
                    DNICall.objects.filter(pk=pc.pk).update(started_at=timezone.now() - timedelta(seconds=seconds))
                Client().post('/api/twilio/asterisk/call-ended/', data=json.dumps(
                    {'call_log_id': str(row.id), 'duration': seconds, 'answered': True, 'recording_url': '',
                     'dialed_seconds': seconds + 2, 'dial_status': 'ANSWER'}),
                    content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        return r, row

    # ── the website script ───────────────────────────────────────────────────
    def test_script_is_served_and_pool_has_a_public_key(self):
        p = self.pool()
        self.assertTrue(p['public_key'].startswith('pk_'))
        r = Client().get('/api/dni/avx-dni.js')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/javascript')
        self.assertIn('/api/dni/v1/number', r.content.decode())

    def test_visitor_keeps_their_number_and_others_get_the_next_free_one(self):
        p = self.pool()
        a = self.number(p, 'visitor-aaaa').json()
        self.assertFalse(a['fallback'])
        self.assertEqual(self.number(p, 'visitor-aaaa').json()['number'], a['number'])
        b = self.number(p, 'visitor-bbbb').json()
        self.assertNotEqual(b['number'], a['number'])
        self.assertEqual(a['display'][:2], '1-')
        s = DNISession.objects.get(visitor_id='visitor-aaaa')
        self.assertEqual((s.utm_source, s.utm_term, s.gclid, s.sub_id), ('bing', 'medicare', 'G1', 's7'))
        self.assertEqual(s.captured, {'gclid': 'G1', 'utm_source': 'bing', 'utm_term': 'medicare'})

    def test_full_pool_shows_the_fallback_and_counts_a_miss(self):
        p = self.pool(size=1)
        self.number(p, 'visitor-aaaa')
        miss = self.number(p, 'visitor-bbbb').json()
        self.assertTrue(miss['fallback'])
        self.assertEqual(miss['number'], '8005550000')
        page = self.api.get(f"/api/dni/pools/{p['id']}").json()
        self.assertEqual(page['misses_today'], 1)
        self.assertEqual(page['impressions_today'], 2)
        self.assertEqual(page['status_label'], 'attention')
        self.assertEqual(page['snippet_seen_host'], 'lp.example.com')

    def test_closed_page_holds_the_number_then_it_rests(self):
        p = self.pool(size=1, cooldown_seconds=120, closed_browser_delay_seconds=30)
        self.number(p, 'visitor-aaaa')
        self.assertTrue(Client().post(f"/api/dni/v1/release?pool={p['public_key']}&vid=visitor-aaaa").json()['ok'])
        s = DNISession.objects.get(visitor_id='visitor-aaaa')
        self.assertLessEqual(s.expires_at, timezone.now() + timedelta(seconds=31))
        # Held for 30s, then the visit ends: the number rests for the cool-down.
        DNISession.objects.filter(pk=s.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertTrue(self.number(p, 'visitor-bbbb').json()['fallback'])
        DNISession.objects.filter(pk=s.pk).update(expires_at=timezone.now() - timedelta(seconds=121))
        self.assertFalse(self.number(p, 'visitor-cccc').json()['fallback'])

    def test_ping_keeps_the_visit(self):
        p = self.pool()
        self.number(p, 'visitor-aaaa')
        s = DNISession.objects.get(visitor_id='visitor-aaaa')
        DNISession.objects.filter(pk=s.pk).update(expires_at=timezone.now() + timedelta(seconds=10))
        r = Client().get('/api/dni/v1/ping', {'pool': p['public_key'], 'vid': 'visitor-aaaa'}).json()
        self.assertTrue(r['ok'])
        s.refresh_from_db()
        self.assertGreater(s.expires_at, timezone.now() + timedelta(minutes=4))
        self.assertFalse(Client().get('/api/dni/v1/ping', {'pool': p['public_key'], 'vid': 'nobody-here'}).json()['ok'])

    def test_least_recently_used_number_first(self):
        p = self.pool(cooldown_seconds=0)
        first = self.number(p, 'visitor-aaaa').json()['number']
        DNISession.objects.all().update(status=DNISession.Status.EXPIRED, expires_at=timezone.now() - timedelta(hours=1))
        DNINumber.objects.all().update(status=DNINumber.Status.AVAILABLE)
        self.assertNotEqual(self.number(p, 'visitor-bbbb').json()['number'], first)

    def test_wrong_website_paused_pool_and_bad_requests(self):
        p = self.pool(allowed_domains=['example.com'])
        self.assertTrue(self.number(p, 'visitor-aaaa', origin='https://evil.com').json()['fallback'])
        self.assertEqual(DNISession.objects.count(), 0)
        self.assertEqual(Client().get('/api/dni/v1/number', {'pool': p['public_key'], 'vid': 'x'}).status_code, 400)
        self.api.patch(f"/api/dni/pools/{p['id']}", {'status': 'paused'}, content_type='application/json')
        self.assertEqual(self.number(p, 'visitor-aaaa').status_code, 404)

    def test_bot_asking_for_many_numbers_gets_the_fallback(self):
        p = self.pool(protection={'assign_per_ip_10m': 2})
        DNISession.objects.all().delete()
        answers = [self.number(p, f'visitor-{i:04d}').json()['fallback'] for i in range(3)]
        self.assertEqual(answers, [False, False, True])

    def test_script_cors_on_the_public_calls(self):
        p = self.pool()
        r = self.number(p, 'visitor-aaaa', origin='https://lp.example.com')
        self.assertEqual(r.get('Access-Control-Allow-Origin'), 'https://lp.example.com')
        pre = Client().options('/api/dni/v1/number', HTTP_ORIGIN='https://lp.example.com',
                               HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET')
        self.assertEqual(pre.get('Access-Control-Allow-Origin'), 'https://lp.example.com')

    # ── landing pages ────────────────────────────────────────────────────────
    def test_page_numbers_found_with_where_they_are(self):
        found = find_numbers(PAGE)
        self.assertEqual({f['number'] for f in found}, {'8005550000', '8885551212'})
        labels = {f['label'] for f in found}
        self.assertIn('Call button', labels)
        self.assertIn('Headline', labels)
        self.assertIn('Footer', labels)
        self.assertNotIn('8005559999', {f['number'] for f in found})  # inside <script>

    def test_lander_scanned_on_create_and_its_numbers_swapped(self):
        with mock.patch('dni.scanner.fetch', return_value=PAGE):
            p = self.pool(landers=[{'url': 'lp.example.com/offer'}])
        page = self.api.get(f"/api/dni/pools/{p['id']}").json()
        self.assertEqual(page['landers_count'], 1)
        self.assertEqual(page['landers'][0]['url'], 'https://lp.example.com/offer')
        self.assertIn('8885551212', page['swap_numbers'])
        lid = page['landers'][0]['id']
        r = self.api.patch(f'/api/dni/landers/{lid}', {'swap': ['1-800-555-0000']}, content_type='application/json')
        self.assertEqual(r.json()['swap'], ['8005550000'])
        self.assertEqual(self.number(p, 'visitor-aaaa').json()['swap'], ['8005550000'])
        with mock.patch('dni.scanner.fetch', return_value='<p>nothing</p>'):
            self.assertEqual(self.api.post(f'/api/dni/landers/{lid}/rescan').json()['found'], [])
        self.assertEqual(self.api.delete(f'/api/dni/landers/{lid}').status_code, 200)
        self.assertEqual(DNILander.objects.count(), 0)

    def test_scan_refuses_private_addresses(self):
        for url in ('http://127.0.0.1/admin', 'http://localhost:8000/', 'http://10.0.0.5/'):
            r = self.api.post('/api/dni/scan', {'url': url}, content_type='application/json')
            self.assertEqual(r.status_code, 400, url)
        with mock.patch('dni.scanner.fetch', return_value=PAGE):
            r = self.api.post('/api/dni/scan', {'url': 'https://lp.example.com'}, content_type='application/json')
        self.assertEqual(len(r.json()['found']), 3)  # the button's link and text are one place

    # ── calls and spam rules ─────────────────────────────────────────────────
    def test_call_on_a_pool_number_is_tied_to_the_visit(self):
        p = self.pool()
        n = self.number(p, 'visitor-aaaa').json()['number']
        r, row = self.call('+1' + n)
        self.assertNotEqual(r.get('action'), 'hangup', r)
        pc = DNICall.objects.get(call_log=row)
        self.assertEqual(pc.session.visitor_id, 'visitor-aaaa')
        self.assertEqual(pc.duration_sec, 30)
        page = self.api.get(f"/api/dni/pools/{p['id']}").json()
        self.assertEqual(page['attributed_today'], 1)
        self.assertEqual(page['calls_by_source'], [{'source': 'bing / medicare', 'calls': 1}])
        calls = self.api.get(f"/api/dni/pools/{p['id']}/calls").json()
        self.assertEqual(calls[0]['source'], 'bing / medicare')

    def test_repeat_caller_is_blocked_on_the_pool_campaign(self):
        self.pool(protection={'max_calls_per_caller_10m': 2})
        for _ in range(2):
            r, _row = self.call('+18885550101', caller='+15551234567')
            self.assertNotEqual(r.get('action'), 'hangup', r)
        r, row = self.call('+18885550101', caller='+15551234567')
        self.assertEqual(r['reason'], 'number_pool_caller_rate')
        self.assertIsNone(row)
        b = Blacklist.objects.get(phone_number='+15551234567')
        self.assertEqual((b.reason, b.campaign_id), (Blacklist.Reason.AUTO, self.campaign.id))
        self.assertGreater(b.expires_at, timezone.now() + timedelta(hours=23))

    def test_call_flood_pulls_the_number_out_of_rotation(self):
        p = self.pool(protection={'number_flood_per_min': 3})
        for _ in range(3):
            self.call('+18885550102')
        r, _row = self.call('+18885550102')
        self.assertEqual(r['reason'], 'number_pool_number_flood')
        n = DNINumber.objects.get(number__endswith='8885550102')
        self.assertEqual(n.status, DNINumber.Status.QUARANTINED)
        r, _row = self.call('+18885550102')  # the attack stays on that number
        self.assertEqual(r['reason'], 'number_pool_quarantined')
        page = self.api.get(f"/api/dni/pools/{p['id']}").json()
        self.assertEqual(page['spam']['numbers_pulled_for_flooding'], 1)
        self.assertEqual(page['numbers_quarantined'], 1)
        # Restored by hand from the pool page.
        r = self.api.post(f"/api/dni/pools/{p['id']}/numbers/{n.id}/status", {'status': 'available'},
                          content_type='application/json')
        self.assertEqual(r.json()['status'], 'available')
        r, _row = self.call('+18885550102')
        self.assertNotEqual(r.get('action'), 'hangup', r)

    def test_pulled_number_is_never_shown_to_visitors(self):
        p = self.pool(size=1)
        n = DNINumber.objects.get(pool_id=p['id'])
        self.api.post(f"/api/dni/pools/{p['id']}/numbers/{n.id}/status", {'status': 'quarantined', 'reason': 'Complaint'},
                      content_type='application/json')
        self.assertTrue(self.number(p, 'visitor-aaaa').json()['fallback'])

    def test_silent_short_callers_are_blocked(self):
        self.pool(protection={'short_calls_per_caller_30m': 3, 'max_calls_per_caller_10m': 0})
        for _ in range(3):
            self.call('+18885550103', caller='+15559876543', seconds=2)
        b = Blacklist.objects.get(phone_number='+15559876543')
        self.assertTrue(b.notes.startswith('Number pool: repeated silent/short calls'))

    def test_anonymous_callers_when_switched_on(self):
        self.pool(protection={'block_anonymous': True})
        r, _row = self.call('+18885550101', caller='anonymous')
        self.assertEqual(r['reason'], 'number_pool_anonymous')

    def test_calls_not_on_a_pool_number_are_untouched(self):
        other = PhoneNumber.objects.create(organization=self.org, number='+18885559999', vendor='Other',
                                           campaign=self.campaign, status='active')
        self.pool(protection={'max_calls_per_caller_10m': 1})
        for _ in range(3):
            r, row = self.call(other.number, caller='+15551112222')
            self.assertIsNotNone(row)
        self.assertEqual(DNICall.objects.count(), 0)

    def test_a_broken_pool_check_never_drops_a_call(self):
        self.pool()
        with mock.patch('dni.protection.pool_number_for', side_effect=RuntimeError('db')):
            r, row = self.call('+18885550101')
        self.assertIsNotNone(row)

    # ── settings, complaints, advertisers only ───────────────────────────────
    def test_design_settings_saved_and_bounded(self):
        p = self.pool(country='CA', cooldown_seconds=300, closed_browser_delay_seconds=45,
                      capture_params=['gclid', 'sub_id'], protection={'block_hours': 48, 'bogus': 1})
        self.assertEqual((p['country'], p['cooldown_seconds'], p['closed_browser_delay_seconds']), ('CA', 300, 45))
        self.assertEqual(p['capture_params'], ['gclid', 'sub_id'])
        self.assertEqual(p['protection']['block_hours'], 48)
        self.assertNotIn('bogus', p['protection'])
        # The portal's key converter drops the underscore before a digit.
        r = self.api.patch(f"/api/dni/pools/{p['id']}", {'protection': {'max_calls_per_caller10m': 7}},
                           content_type='application/json')
        self.assertEqual(r.json()['protection']['max_calls_per_caller_10m'], 7)
        self.assertEqual(r.json()['protection']['block_hours'], 48)
        r = self.api.patch(f"/api/dni/pools/{p['id']}", {'cooldown_seconds': 60}, content_type='application/json')
        self.assertEqual(r.json()['cooldown_seconds'], 60)
        listed = self.api.get('/api/dni/pools').json()
        rows = listed if isinstance(listed, list) else listed.get('items') or listed.get('data') or listed.get('results')
        self.assertNotIn('number_rows', rows[0])

    def test_complaints(self):
        r = self.api.post('/api/dni/complaints', {'tfn': '+1 (888) 555-0101', 'source': 'Carrier'},
                          content_type='application/json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['tfn'], '8885550101')
        self.assertEqual(len(self.api.get('/api/dni/complaints').json()), 1)
        self.assertEqual(self.api.post('/api/dni/complaints', {'tfn': '1'}, content_type='application/json').status_code, 400)

    def test_advertisers_only(self):
        p = self.pool()
        pub = Publisher.objects.create(organization=self.org, name='P', permissions={'number_creation': True})
        u = User.objects.create(username='pub', email='pub@x.io', organization=self.org, role='publisher',
                                is_active=True, publisher=pub)
        c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')
        for path in (f"/api/dni/pools/{p['id']}/landers", f"/api/dni/pools/{p['id']}/calls", '/api/dni/complaints'):
            self.assertEqual(c.get(path).status_code, 403, path)
        self.assertEqual(c.post('/api/dni/scan', {'url': 'https://x.com'}, content_type='application/json').status_code, 403)

    def test_other_organisations_cannot_see_landers(self):
        with mock.patch('dni.scanner.fetch', return_value=PAGE):
            p = self.pool(landers=[{'url': 'https://lp.example.com'}])
        lid = DNILander.objects.get(pool_id=p['id']).id
        other = Organization.objects.create(name='Other', slug='other')
        u = User.objects.create(username='o', email='o@x.io', organization=other, role='admin', is_active=True)
        c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')
        self.assertEqual(c.delete(f'/api/dni/landers/{lid}').status_code, 404)
