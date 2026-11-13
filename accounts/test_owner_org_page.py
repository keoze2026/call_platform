"""CH-675: the new Organizations page (Sir, 13 Nov: "Org page front end -
change it to the updated version with more details").

Run:  python manage.py test accounts.test_owner_org_page
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import ActivityLog, Organization, User
from accounts.owner_audit import OWNER_MESSAGE, OWNER_VIEWED
from analytics.models import CallRecord
from billing.models import BillingAccount
from phone_numbers.models import PhoneNumber


def client_for(user):
    return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')


@override_settings(MAIN_ORGANIZATION_SLUG='interns', ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class OrgPageTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.ours = Organization.objects.create(name='Avortyx', slug='interns')
        BillingAccount.objects.create(organization=self.ours, balance=Decimal('3000'),
                                      per_minute_rate=Decimal('0.045'))
        self.sir = User.objects.create(username='sir', email='sir@ourteam.io', organization=self.ours,
                                       role='admin', is_superuser=True, is_active=True)
        self.tx = Organization.objects.create(name='Texora', slug='texora', tfn_risk='low')
        self.tx_bill = BillingAccount.objects.create(organization=self.tx, balance=Decimal('2'),
                                                     per_minute_rate=Decimal('0.05'),
                                                     low_balance_threshold=Decimal('10'))
        self.admin = User.objects.create(username='tx', email='tx@texora.io', organization=self.tx,
                                         role='admin', is_active=True, mfa_enabled=False)
        self.empty = Organization.objects.create(name='Keoze', slug='keoze')    # no billing yet
        self.owner = client_for(self.sir)

    def rows(self):
        r = self.owner.get('/api/owner/organizations')
        self.assertEqual(r.status_code, 200, r.content)
        return {x['name']: x for x in r.json()['items']}

    def record(self, **kw):
        base = dict(id=uuid.uuid4(), organization=self.tx, caller_number='+15550001111',
                    called_number='+18888914875', campaign_name='C-07', publisher_name='PUB1')
        base.update(kw)
        return CallRecord.objects.create(**base)

    # ── the list ────────────────────────────────────────────────────────────
    def test_list_has_runway_health_and_main(self):
        rows = self.rows()
        self.assertEqual(rows['Avortyx']['runway_minutes'], 66666)
        self.assertEqual(rows['Avortyx']['health'], 'ok')
        self.assertTrue(rows['Avortyx']['is_main_account'])
        self.assertEqual(rows['Texora']['runway_minutes'], 40)
        self.assertEqual(rows['Texora']['health'], 'low_balance')
        self.assertFalse(rows['Texora']['is_main_account'])
        self.assertEqual(rows['Keoze']['health'], 'billing_not_set')
        self.assertIsNone(rows['Keoze']['runway_minutes'])
        for key in ('calls_today', 'revenue_today', 'per_minute_rate', 'markup_percent', 'users', 'live_now'):
            self.assertIn(key, rows['Texora'])

    def test_health_no_balance_and_suspended(self):
        self.tx_bill.balance = Decimal('0')
        self.tx_bill.save()
        self.assertEqual(self.rows()['Texora']['health'], 'no_balance')
        self.tx.is_active = False
        self.tx.save()
        self.assertEqual(self.rows()['Texora']['health'], 'suspended')

    # ── the account page ────────────────────────────────────────────────────
    def test_overview_flow_and_checks(self):
        self.record(buyer_id=uuid.uuid4(), buyer_name='HOZ', status='completed', duration_seconds=120)
        self.record(buyer_id=uuid.uuid4(), buyer_name='HOZ', status='no_answer')
        self.record(status='failed')
        PhoneNumber.objects.create(organization=self.tx, number='+18888914875', vendor='Other',
                                   status='released', released_at=timezone.now() - timedelta(hours=3),
                                   retired_reason='expired')
        r = self.owner.get(f'/api/owner/organizations/{self.tx.id}/overview')
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d['flow'], {'from_publishers': 3, 'routed_to_buyers': 2, 'connected': 1})
        self.assertEqual(d['calls_today'], 3)
        self.assertEqual(d['health'], 'low_balance')
        codes = {c['code']: c for c in d['checks']}
        self.assertIn('+18888914875', codes['tfn_expired']['detail'])
        self.assertEqual(codes['tfn_expired']['level'], 'action')
        self.assertEqual(codes['2fa_off']['title'], '2FA off for 1 user(s)')
        self.assertIn('low_balance', codes)
        self.assertIn('no_tfns', codes)

    def test_traffic_by_campaign_publisher_and_buyer(self):
        b = uuid.uuid4()
        self.record(buyer_id=b, buyer_name='HOZ', status='completed', duration_seconds=90,
                    platform_cost=Decimal('0.0750'))
        self.record(buyer_id=b, buyer_name='HOZ', status='no_answer')
        self.record(status='failed', buyer_name='')
        r = self.owner.get(f'/api/owner/organizations/{self.tx.id}/traffic')
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()['rows']
        hoz = next(x for x in rows if x['buyer'] == 'HOZ')
        self.assertEqual((hoz['campaign'], hoz['publisher'], hoz['calls_in'], hoz['calls_out'], hoz['connected']),
                         ('C-07', 'PUB1', 2, 2, 1))
        self.assertEqual((hoz['minutes'], hoz['spend']), (1.5, '0.08'))
        self.assertEqual(self.owner.get(f'/api/owner/organizations/{self.tx.id}/traffic?date=x').status_code, 400)

    def test_numbers_source_and_state(self):
        PhoneNumber.objects.create(organization=self.tx, number='+18885550001', vendor='Other', sale_risk='low')
        PhoneNumber.objects.create(organization=self.tx, number='+18885550002', vendor='Other')
        self.record(called_number='+18885550001')
        r = self.owner.get(f'/api/owner/organizations/{self.tx.id}/numbers')
        self.assertEqual(r.status_code, 200, r.content)
        rows = {x['number']: x for x in r.json()['rows']}
        self.assertEqual((rows['+18885550001']['source'], rows['+18885550001']['risk'],
                          rows['+18885550001']['calls_today'], rows['+18885550001']['state']),
                         ('avortyx', 'low', 1, 'live'))
        self.assertEqual(rows['+18885550002']['source'], 'own')

    def test_lookup_tfn_caller_and_not_found(self):
        PhoneNumber.objects.create(organization=self.tx, number='+18885550001', vendor='Other', sale_risk='low')
        self.record(called_number='+18885550001', caller_number='+15617778888')
        self.record(called_number='+18885550001', caller_number='+15617778888')
        t = self.owner.get('/api/owner/lookup', {'number': '(888) 555-0001'}).json()
        self.assertTrue(t['found'])
        self.assertEqual((t['tfn']['account'], t['tfn']['total_calls'], t['tfn']['source']), ('Texora', 2, 'avortyx'))
        c = self.owner.get('/api/owner/lookup?number=5617778888').json()
        self.assertEqual(c['caller']['total_calls'], 2)
        self.assertEqual(c['caller']['by_account'], [{'account': 'Texora', 'calls': 2}])
        self.assertFalse(self.owner.get('/api/owner/lookup?number=2025550199').json()['found'])

    def test_users_carry_2fa_and_only_the_owner_gets_in(self):
        d = self.owner.get(f'/api/owner/organizations/{self.tx.id}').json()
        self.assertEqual(d['users'][0]['mfa_enabled'], False)
        for path in ('overview', 'traffic', 'numbers', 'transactions'):
            self.assertEqual(client_for(self.admin).get(f'/api/owner/organizations/{self.tx.id}/{path}').status_code,
                             403, path)
        self.assertEqual(client_for(self.admin).get('/api/owner/lookup?number=8885550001').status_code, 403)

    # ── Message client ──────────────────────────────────────────────────────
    def test_message_reaches_their_portal_until_seen(self):
        r = self.owner.post(f'/api/owner/organizations/{self.tx.id}/message', {'text': 'Please top up today.'},
                            content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        theirs = client_for(self.admin)
        m = theirs.get('/api/notifications/owner-messages').json()
        self.assertEqual(m['unseen_count'], 1)
        self.assertEqual((m['messages'][0]['text'], m['messages'][0]['from']), ('Please top up today.', 'Avortyx'))
        self.assertEqual(theirs.post('/api/notifications/owner-messages/seen', {},
                                     content_type='application/json').json()['unseen_count'], 0)
        feed = theirs.get('/api/accounts/workspace/activity').json()['items']
        row = next(i for i in feed if i['action'] == OWNER_MESSAGE)
        self.assertEqual((row['actor_name'], row['action_label']), ('Avortyx', 'Message from the platform owner'))
        self.assertNotIn('sir@ourteam.io', str(feed))
        self.assertEqual(self.owner.post(f'/api/owner/organizations/{self.tx.id}/message', {'text': ' '},
                                         content_type='application/json').status_code, 400)

    # ── Open their portal (read-only) ───────────────────────────────────────
    def view_client(self):
        r = self.owner.post(f'/api/owner/organizations/{self.tx.id}/view-token')
        self.assertEqual(r.status_code, 200, r.content)
        return Client(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")

    def test_their_portal_reads_and_refuses_every_change(self):
        view = self.view_client()
        me = view.get('/api/accounts/me')
        self.assertEqual(me.status_code, 200, me.content)
        self.assertEqual(me.json()['email'], 'tx@texora.io')
        for method in ('post', 'patch', 'put', 'delete'):
            r = getattr(view, method)('/api/campaigns/', {}, content_type='application/json')
            self.assertEqual(r.status_code, 403, method)
            self.assertEqual(r.json()['code'], 'read_only_view')
        self.assertEqual(view.post('/api/notifications/owner-messages/seen', {},
                                   content_type='application/json').status_code, 403)

    def test_every_view_is_logged_in_both_logs_and_not_as_the_client(self):
        view = self.view_client()
        view.get('/api/accounts/me')
        view.get('/api/accounts/me')                       # same area within 10 min: one line
        view.get('/api/accounts/workspace/roles')          # first visit makes the built-in roles
        mine = ActivityLog.objects.filter(organization=self.ours, action=OWNER_VIEWED)
        theirs = ActivityLog.objects.filter(organization=self.tx, action=OWNER_VIEWED)
        what = sorted(theirs.values_list('metadata__owner_action', flat=True))
        self.assertEqual(what, ['opened their portal (read-only)', 'viewed their portal: accounts/me',
                                'viewed their portal: accounts/workspace'])
        self.assertEqual(mine.count(), 3)
        self.assertFalse(ActivityLog.objects.filter(user=self.admin).exists())   # nothing as the client

    def test_no_pass_for_our_account_or_once_the_owner_is_not_the_owner(self):
        self.assertEqual(self.owner.post(f'/api/owner/organizations/{self.ours.id}/view-token').status_code, 400)
        view = self.view_client()
        self.sir.is_superuser = False
        self.sir.save()
        self.assertEqual(view.get('/api/accounts/me').status_code, 401)
        self.assertEqual(client_for(self.admin).post(f'/api/owner/organizations/{self.tx.id}/view-token')
                         .status_code, 403)

    def test_an_ordinary_login_is_untouched(self):
        r = client_for(self.admin).post('/api/notifications/owner-messages/seen', {},
                                        content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
