"""Customer accounts 1c: which account every piece of portal code runs as.

The policies themselves are tested on PostgreSQL (the whole suite under
config/test_settings_rls on Server B, and `manage.py rls --prove`); this suite
(SQLite) proves the account the code hands the database at every step.

Run:  python manage.py test accounts.test_rls
"""
from unittest import mock

from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from ninja import NinjaAPI, Router
from ninja.testing import TestClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts import rls, rls_sql
from accounts.api import APIKeyAuth, JWTAuth
from accounts.models import Organization, User

seen = []

api = NinjaAPI(urls_namespace='rls-test')
r = Router()


@r.get('/public', auth=None)
def public(request):
    return {'account': rls.current()}


@r.get('/private', auth=JWTAuth())
def private(request):
    return {'account': rls.current(), 'marked': getattr(request, '_avx_org', None)}


@r.get('/key', auth=APIKeyAuth())
def key(request):
    return {'account': rls.current()}


@r.get('/owner', auth=JWTAuth())
def owner(request):
    from accounts.owner_api import require_owner
    require_owner(request)
    return {'account': rls.current()}


api.add_router('/', r)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RequestAccountTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.client_ = TestClient(api)
        self.org = Organization.objects.create(name='Test A', slug='test-a')

    def token(self, **kw):
        u = User.objects.create(username=kw.get('email', 'a@x.io'), email=kw.get('email', 'a@x.io'),
                                role=kw.get('role', 'admin'), is_superuser=kw.get('is_superuser', False),
                                organization=kw.get('organization', self.org))
        return {'Authorization': f'Bearer {AccessToken.for_user(u)}'}

    def get(self, path, headers=None):
        with rls.scope(rls.NONE):               # as TenantMiddleware leaves it for the view
            r = self.client_.get(path, headers=headers or {}, path_info=f'/api/rls-test{path}')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_outside_a_request_is_system(self):
        self.assertEqual(rls.current(), rls.SYSTEM)

    def test_an_endpoint_without_login_runs_as_system(self):
        self.assertEqual(self.get('/public')['account'], rls.SYSTEM)

    def test_a_logged_in_request_runs_as_its_account(self):
        body = self.get('/private', self.token())
        self.assertEqual(body, {'account': str(self.org.id), 'marked': str(self.org.id)})

    def test_a_partner_login_runs_as_its_workspace(self):
        body = self.get('/private', self.token(email='b@x.io', role='buyer'))
        self.assertEqual(body['account'], str(self.org.id))

    def test_a_login_without_an_account_sees_nothing(self):
        body = self.get('/private', self.token(email='h@x.io', role='agent', organization=None))
        self.assertEqual(body['account'], rls.NONE)

    def test_a_refused_login_never_gets_an_account(self):
        with rls.scope(rls.NONE):
            r = self.client_.get('/private', headers={'Authorization': 'Bearer nonsense'},
                                 path_info='/api/rls-test/private')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(rls.current(), rls.SYSTEM)       # nothing left behind

    def test_an_api_key_runs_as_the_key_account(self):
        from accounts.services import APIKeyService
        u = User.objects.create(username='k@x.io', email='k@x.io', role='admin', organization=self.org)
        raw = APIKeyService.create(u, 'k')['key']
        self.assertEqual(self.get('/key', {'Authorization': f'Bearer {raw}'})['account'], str(self.org.id))

    def test_the_main_account_is_looked_up_as_system(self):
        # Rehearsal B: under the rules a customer login cannot see our
        # account's row; looked up as that login, "no main account" made
        # every customer login count as main (point 3).
        from accounts.tenancy import main_organization_id
        seen, real = [], Organization.objects.filter

        def spy(*a, **kw):
            seen.append(rls.current())
            return real(*a, **kw)
        with override_settings(MAIN_ORGANIZATION_SLUG='test-a'), \
                mock.patch.object(Organization.objects, 'filter', side_effect=spy), \
                rls.scope('11111111-1111-1111-1111-111111111111'):
            self.assertEqual(main_organization_id(), self.org.id)
        self.assertEqual(seen, [rls.SYSTEM])

    def test_the_owner_pages_look_across_accounts(self):
        self.assertEqual(self.get('/owner', self.token(email='o@x.io', is_superuser=True))['account'],
                         rls.SYSTEM)

    def test_a_non_owner_on_an_owner_page_is_refused(self):
        headers = self.token(email='n@x.io')          # the login is made before the request, as system
        with rls.scope(rls.NONE):
            r = self.client_.get('/owner', headers=headers, path_info='/api/rls-test/owner')
        self.assertEqual(r.status_code, 403)


class MiddlewareTests(SimpleTestCase):
    def run_view(self, path, view):
        mw = rls.TenantMiddleware(view)
        return mw(RequestFactory().get(path))

    def seen_by(self, path):
        out = {}

        def view(request):
            out['account'] = rls.current()
            return HttpResponse()
        self.run_view(path, view)
        return out['account']

    def test_a_view_starts_with_no_account(self):
        self.assertEqual(self.seen_by('/api/campaigns/'), rls.NONE)
        self.assertEqual(rls.current(), rls.SYSTEM)

    def test_call_routing_callbacks_run_as_system(self):
        self.assertEqual(self.seen_by('/api/twilio/asterisk/route/'), rls.SYSTEM)

    def test_the_recording_link_check_runs_as_system(self):
        # Rehearsal B: nginx's check (no login) must find any account's call.
        self.assertEqual(self.seen_by('/api/recordings/check'), rls.SYSTEM)

    def test_a_streamed_file_is_made_as_the_request_account(self):
        org = '11111111-1111-1111-1111-111111111111'

        def view(request):
            request._avx_org = org
            return StreamingHttpResponse(rls.current() for _ in range(2))
        response = self.run_view('/api/analytics/calls/export', view)
        self.assertEqual(b''.join(response.streaming_content).decode(), org * 2)


class FakeConn:
    vendor = 'postgresql'

    def __init__(self):
        self.in_atomic_block = False
        self.execute_wrappers = []
        self.sent = []
        conn = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params):
                conn.sent.append(tuple(params))
        self.connection = mock.Mock(cursor=Cursor)


class ConnectionTests(SimpleTestCase):
    def query(self, conn):
        return rls._guard(lambda *a: 'result', 'select 1', None, False, {'connection': conn})

    def test_the_setting_is_sent_once_per_change_outside_a_transaction(self):
        conn = FakeConn()
        rls._on_connect(None, conn)
        self.assertIn(rls._guard, conn.execute_wrappers)
        self.assertEqual(self.query(conn), 'result')
        self.query(conn)
        with rls.scope('11111111-1111-1111-1111-111111111111'):
            self.query(conn)
            self.query(conn)
        self.assertEqual(conn.sent, [('*', False), ('11111111-1111-1111-1111-111111111111', False)])

    def test_inside_a_transaction_it_is_sent_on_every_query_for_that_transaction_only(self):
        conn = FakeConn()
        rls._on_connect(None, conn)
        self.query(conn)
        conn.in_atomic_block = True
        with rls.scope(rls.NONE):
            self.query(conn)
            self.query(conn)
        self.assertEqual(conn.sent, [('*', False), ('', True), ('', True)])
        self.assertEqual(conn._avx_org, '*')      # the session value, untouched by the transaction

    def test_transaction_control_is_never_preceded_by_the_setting(self):
        # Rehearsal B: after an error the transaction accepts only the
        # rollback; the setting sent first made the rollback itself fail.
        conn = FakeConn()
        rls._on_connect(None, conn)
        conn.in_atomic_block = True
        for sql in ('ROLLBACK TO SAVEPOINT "s1"', 'SAVEPOINT "s2"', 'RELEASE SAVEPOINT "s2"', '  rollback'):
            self.assertEqual(rls._guard(lambda *a: 'done', sql, None, False, {'connection': conn}), 'done')
        self.assertEqual(conn.sent, [])
        self.query(conn)                              # an ordinary query still gets it
        self.assertEqual(conn.sent, [('*', True)])

    def test_a_new_connection_starts_again(self):
        conn = FakeConn()
        rls._on_connect(None, conn)
        self.query(conn)
        rls._on_connect(None, conn)                  # reconnected
        self.query(conn)
        self.assertEqual(len(conn.sent), 2)
        self.assertEqual(conn.execute_wrappers.count(rls._guard), 1)

    def test_only_account_ids_are_accepted(self):
        with self.assertRaises(ValueError):
            rls.set_account("x'; drop table users; --")


class PolicyTests(TestCase):
    def test_every_account_table_gets_a_policy(self):
        from django.apps import apps
        tables = rls_sql.account_tables(apps.get_models())
        self.assertGreaterEqual(len(tables), 79)          # 78 account tables + organizations
        sql = rls_sql.statements(tables, 'interns')
        for t in tables:
            self.assertIn(f'create policy avx_account on "{t}"', '\n'.join(sql))
        self.assertIn('phone_numbers_routingrate', '\n'.join(sql))

    def test_nothing_happens_off_postgresql(self):
        from django.db import connection
        if connection.vendor == 'postgresql':
            self.assertEqual(rls_sql.missing(connection), [])      # there: every table covered
            return
        self.assertEqual(rls_sql.apply(connection), 0)
        self.assertEqual(rls_sql.missing(connection), [])
