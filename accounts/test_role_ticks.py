"""Every tick on the Roles screen does what it says, for Viewer and Manager.

The owner's test: take access as Viewer and Manager, tick and untick every
option, and make sure they really work. This does exactly that, against the
live API, for all of the endpoints that carry a tick:

  - nothing ticked: every ticked endpoint is closed
  - exactly the ticks an endpoint needs: it is open (no 403 of any kind),
    so a Viewer with an Edit tick can edit and an old fixed rule cannot
    override it
  - everything ticked except one needed tick: it is closed again
  - workspace administration (members, roles, sessions, activity) stays
    admin only whatever is ticked

And a guard: a new endpoint nobody classified fails the build, so a future
screen cannot ship with no tick behind it.
"""
import logging
import re
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts import workspace_permissions as wp
from accounts.models import CustomRole, Organization, User
from accounts.route_permissions import ADMIN, requirements
from config.api import api

UID = '00000000-0000-0000-0000-000000000001'

# Endpoints that carry no tick on purpose, with the reason. Anything that
# `requirements()` leaves open and is not matched here fails the guard test.
OPEN_BY_DESIGN = [
    r'^/accounts/(me|logout|change-password|mfa|password|set-password|api-keys|roles|partner-permissions|activity)',
    r'^/accounts/workspace$',                 # basic workspace info for the header
    r'^/accounts/\{kind\}/',                  # partner settings: guarded in the handler (admin)
    r'^/notifications/(events|preferences)',  # a person's own alert preferences
    r'^/notifications/deleted-tfns',          # a person's own TFN Deleted notice and its Seen marker (CH-179)
    r'^/spam/blocked-callers$',               # which callers to colour red in any call log (CH-185)
    r'^/carriers',                            # list: Vendors View or Tracking Numbers View (handler)
    r'^/spam/shields',                        # decided per shield type in the handler
    r'^/(kyc|referrals|security|contact|support|owner)',
    r'^/marketplace',                         # listings are edited by platform staff (handler)
    r'^/accounts/access-requests',            # platform staff only (StaffAuth)
    r'^/white-label',                         # reads are open; writes are admin (see ADMIN rule)
    r'^/ai/',                                 # covered by the AI rules
    r'^/routing/calls',                       # covered by the call-flows rule
    r'^/dni/(snippet|assign)',                # public tracking script endpoints
    r'^/ivr/webhook',                         # called by the telephony side
    r'^/webhooks/conversion',                 # called by traffic partners
    r'^/rtb/bid',
    r'^/billing/.*webhook',
    r'^/accounts/(register|login|verify-mfa|refresh)',
]


def _ops():
    out = []
    for path, methods in api.get_openapi_schema()['paths'].items():
        for method, op in methods.items():
            if method not in ('get', 'post', 'put', 'patch', 'delete'):
                continue
            if not op.get('security'):
                continue
            if path.startswith('/api/stats'):
                continue  # API-key routes: covered by StatsApiKeyTicks below
            tmpl = path.removeprefix('/api')
            url = re.sub(r'\{[^}]+\}', UID, path)
            out.append({'method': method, 'tmpl': tmpl, 'url': url})
    return out


def _call(client, op):
    kw = {}
    if op['method'] != 'get':
        kw = {'data': '{}', 'content_type': 'application/json'}
    return getattr(client, op['method'])(op['url'], **kw)


def _kind(resp):
    if resp.status_code != 403:
        return 'open'
    try:
        detail = resp.json().get('detail', '')
    except Exception:
        detail = ''
    if "doesn't have access" in detail:
        return 'tick'
    if 'Ask an admin' in detail:
        return 'admin'
    return 'other'


class RouteTableGuard(SimpleTestCase):
    def test_every_endpoint_is_classified(self):
        unclassified = []
        for op in _ops():
            if requirements(op['method'], op['tmpl']):
                continue
            if any(re.match(p, op['tmpl']) for p in OPEN_BY_DESIGN):
                continue
            unclassified.append(f"{op['method'].upper()} {op['tmpl']}")
        self.assertEqual(
            unclassified, [],
            'These endpoints have no tick behind them. Add them to RULES in '
            'accounts/route_permissions.py (or to OPEN_BY_DESIGN with a reason).')

    def test_every_tick_in_the_table_exists(self):
        keys = set(wp.ALL_KEYS)
        for op in _ops():
            need = requirements(op['method'], op['tmpl'])
            if need and need != ADMIN:
                for area, action in need:
                    self.assertIn(f'{area}.{action}', keys, f"{op['method']} {op['tmpl']}")


@override_settings(ALLOWED_HOSTS=['*'], DEBUG=False,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class EveryTickWorks(TransactionTestCase):
    maxDiff = None
    # TransactionTestCase: a dummy request that trips a database error inside
    # its own handler would otherwise poison the surrounding test transaction.
    def setUp(self):
        self.org = Organization.objects.create(name='TickOrg', slug='tickorg')
        wp.ensure_builtin_roles(self.org)
        self.clients = {}
        for role in ('manager', 'viewer'):
            user = User.objects.create(username=role, email=f'{role}@tick.io',
                                       organization=self.org, role=role, is_active=True)
            self.clients[role] = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')
        # The rate limiter is not what is under test; hundreds of calls in a row would trip it.
        patcher = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Dummy requests must never reach Twilio, Stripe or anything else outside.
        net = mock.patch('requests.sessions.Session.request', side_effect=ConnectionError('blocked in tests'))
        net.start()
        self.addCleanup(net.stop)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def _set(self, role, keys):
        row = CustomRole.objects.get(organization=self.org, slug=role)
        row.capabilities = sorted(keys)
        row.save()

    def _ticked_ops(self):
        out = []
        for op in _ops():
            need = requirements(op['method'], op['tmpl'])
            if need and need != ADMIN:
                out.append((op, need))
        return out

    def test_nothing_ticked_closes_everything(self):
        for role, client in self.clients.items():
            self._set(role, set())
            bad = [f"{role}: {op['method'].upper()} {op['tmpl']} is open with nothing ticked"
                   for op, need in self._ticked_ops() if _kind(_call(client, op)) != 'tick']
            self.assertEqual(bad, [])

    def test_exactly_the_needed_ticks_open_it(self):
        for role, client in self.clients.items():
            bad = []
            for op, need in self._ticked_ops():
                self._set(role, {f'{a}.{x}' for a, x in need})
                kind = _kind(_call(client, op))
                if kind != 'open':
                    bad.append(f"{role}: {op['method'].upper()} {op['tmpl']} is {kind} with {need} ticked")
            self.assertEqual(bad, [])

    def test_unticking_any_needed_tick_closes_it(self):
        for role, client in self.clients.items():
            bad = []
            for op, need in self._ticked_ops():
                for area, action in need:
                    self._set(role, set(wp.ALL_KEYS) - {f'{area}.{action}'})
                    if _kind(_call(client, op)) != 'tick':
                        bad.append(f"{role}: {op['method'].upper()} {op['tmpl']} still open without {area}.{action}")
            self.assertEqual(bad, [])

    def test_workspace_administration_stays_admin_only(self):
        for role, client in self.clients.items():
            self._set(role, set(wp.ALL_KEYS))
            bad = [f"{role}: {op['method'].upper()} {op['tmpl']}" for op in _ops()
                   if requirements(op['method'], op['tmpl']) == ADMIN and _kind(_call(client, op)) != 'admin']
            self.assertEqual(bad, [])

    def test_every_tick_is_wired_or_documented(self):
        """A tick with no endpoint behind it is reported, not silently ignored."""
        wired = set()
        for op, need in self._ticked_ops():
            wired |= {f'{a}.{x}' for a, x in need}
        wired |= {'vendors.view', 'voip-shield.view', 'voip-shield.edit', 'voip-shield.delete', 'tcpa-shield.view'}  # shields, in the handler
        no_feature_yet = {
            'groups.view', 'groups.edit', 'groups.delete',
            'custom-parameters.view', 'custom-parameters.edit', 'custom-parameters.delete',
            'leads.view', 'leads.delete', 'dispute.view', 'global-offers.view', 'ping-post-log.view',
        }
        self.assertEqual(set(wp.ALL_KEYS) - wired - no_feature_yet, set(),
                         'a tick on the Roles screen guards nothing')


@override_settings(ALLOWED_HOSTS=['*'], DEBUG=False,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class TicksOverrideTheOldFixedRule(TestCase):
    """A Viewer with an Edit tick can edit; a custom role holds what it is ticked."""

    def setUp(self):
        patcher = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.org = Organization.objects.create(name='TickOrg2', slug='tickorg2')
        wp.ensure_builtin_roles(self.org)
        from campaigns.models import Campaign
        self.campaign = Campaign.objects.create(organization=self.org, name='Before')

    def _client(self, role):
        user = User.objects.create(username=f'u-{role}', email=f'{role}@t.io',
                                   organization=self.org, role=role, is_active=True)
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def _patch(self, client):
        return client.patch(f'/api/campaigns/{self.campaign.id}', data='{"name": "After"}',
                            content_type='application/json')

    def test_viewer_default_cannot_edit(self):
        resp = self._patch(self._client('viewer'))
        self.assertEqual(resp.status_code, 403)

    def test_viewer_with_edit_tick_can_edit(self):
        row = CustomRole.objects.get(organization=self.org, slug='viewer')
        row.capabilities = ['campaigns.view', 'campaigns.edit-campaign']
        row.save()
        resp = self._patch(self._client('viewer'))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.name, 'After')

    def test_manager_loses_edit_when_unticked(self):
        row = CustomRole.objects.get(organization=self.org, slug='manager')
        row.capabilities = [k for k in row.capabilities if k != 'campaigns.edit-campaign']
        row.save()
        self.assertEqual(self._patch(self._client('manager')).status_code, 403)

    def test_custom_role_holds_exactly_its_ticks(self):
        role = CustomRole.objects.create(organization=self.org, name='Role Test',
                                         capabilities=['campaigns.view', 'campaigns.edit-campaign'])
        client = self._client(str(role.id))
        self.assertEqual(self._patch(client).status_code, 200)
        resp = client.delete(f'/api/campaigns/{self.campaign.id}')
        self.assertEqual(resp.status_code, 403)


@override_settings(ALLOWED_HOSTS=['*'], DEBUG=False,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ShieldTicks(TestCase):
    """VoIP and TCPA shields are separate pages with separate ticks."""

    def setUp(self):
        patcher = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        from spam_protection.models import Shield
        self.org = Organization.objects.create(name='ShieldOrg', slug='shieldorg')
        wp.ensure_builtin_roles(self.org)
        self.voip = Shield.objects.create(organization=self.org, name='V', shield_type='voip')
        self.tcpa = Shield.objects.create(organization=self.org, name='T', shield_type='tcpa')
        user = User.objects.create(username='v', email='v@s.io', organization=self.org,
                                   role='viewer', is_active=True)
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def _ticks(self, *keys):
        row = CustomRole.objects.get(organization=self.org, slug='viewer')
        row.capabilities = list(keys)
        row.save()

    def _names(self):
        resp = self.client.get('/api/spam/shields/')
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        rows = body.get('items') or body.get('results') or body.get('data') or []
        return sorted(r['shield_type'] for r in rows)

    def test_list_shows_only_the_kinds_ticked(self):
        self._ticks('voip-shield.view')
        self.assertEqual(self._names(), ['voip'])
        self._ticks('tcpa-shield.view')
        self.assertEqual(self._names(), ['tcpa'])
        self._ticks('voip-shield.view', 'tcpa-shield.view')
        self.assertEqual(self._names(), ['tcpa', 'voip'])
        self._ticks()
        self.assertEqual(self._names(), [])

    def test_single_shield_needs_its_own_view_tick(self):
        self._ticks('voip-shield.view')
        self.assertEqual(self.client.get(f'/api/spam/shields/{self.voip.id}/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/spam/shields/{self.tcpa.id}/').status_code, 403)

    def test_changing_and_deleting_need_the_edit_and_delete_ticks(self):
        self._ticks('voip-shield.view')
        resp = self.client.patch(f'/api/spam/shields/{self.voip.id}/', data='{"name": "X"}',
                                 content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self._ticks('voip-shield.view', 'voip-shield.edit')
        resp = self.client.patch(f'/api/spam/shields/{self.voip.id}/', data='{"name": "X"}',
                                 content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.client.delete(f'/api/spam/shields/{self.voip.id}/').status_code, 403)
        self._ticks('voip-shield.view', 'voip-shield.delete')
        self.assertEqual(self.client.delete(f'/api/spam/shields/{self.voip.id}/').status_code, 200)

    def test_tcpa_changes_need_the_shield_edit_tick_too(self):
        self._ticks('tcpa-shield.view')
        resp = self.client.patch(f'/api/spam/shields/{self.tcpa.id}/', data='{"name": "X"}',
                                 content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self._ticks('tcpa-shield.view', 'voip-shield.edit')
        resp = self.client.patch(f'/api/spam/shields/{self.tcpa.id}/', data='{"name": "X"}',
                                 content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)


class StatsApiKeyTicks(SimpleTestCase):
    """/stats is read with an API key, which carries its owner's role."""

    def test_stats_need_reporting_view(self):
        from types import SimpleNamespace
        from ninja.errors import HttpError
        from accounts.route_permissions import enforce_route
        user = SimpleNamespace(role='viewer', is_superuser=False, organization=None)
        request = SimpleNamespace(method='GET', path_info='/api/stats/campaigns')
        with mock.patch('accounts.workspace_permissions.effective_permissions', return_value=[]):
            with self.assertRaises(HttpError) as ctx:
                enforce_route(request, user)
        self.assertEqual(ctx.exception.status_code, 403)
        request = SimpleNamespace(method='GET', path_info='/api/stats/campaigns')
        with mock.patch('accounts.workspace_permissions.effective_permissions', return_value=['reporting.view']):
            enforce_route(request, user)
