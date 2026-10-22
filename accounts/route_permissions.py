"""Which tick guards which endpoint, in one table.

The Roles screen has 55 ticks. Until now each one only protected the handful
of endpoints whose author remembered to call `require_perm`: 21 ticks guarded
nothing, and with every box unticked a Viewer could still read live calls,
stats, webhooks, notification rules and the member list. A tick that does
nothing is worse than no tick, because the admin believes the door is shut.

The table below is the whole answer to "what does this tick protect". It is
applied once, where the bearer token is turned into a user (`JWTAuth`), so a
new endpoint under a listed prefix is guarded the day it is written and no
handler can forget.

Reading the table:
  - the first rule whose pattern matches the path decides
  - `verbs` maps the HTTP method to the tick: GET reads, POST and PATCH and PUT
    change, DELETE removes
  - a rule may list more than one requirement; every one must be held
  - admins, resellers, superusers and partner logins are never limited here
    (partners are governed by Partner Access, see `partner_permissions.py`)

Handlers keep their own `require_perm` calls. They are the same answer a
second time, which is harmless, and they still protect the code if it is ever
mounted somewhere else.
"""
import re

from ninja.errors import HttpError

# Default method -> action for a "plain" area.
VERBS = {
    'GET': 'view', 'HEAD': 'view', 'OPTIONS': 'view',
    'POST': 'edit', 'PUT': 'edit', 'PATCH': 'edit',
    'DELETE': 'delete',
}

# Marker for endpoints that only an admin of the workspace may use. They have
# no tick: members, roles, sessions and the activity log are administration.
ADMIN = '__admin__'


def _rule(pattern, area, verbs=None, extra=None):
    """pattern: regex on the path after /api, no trailing slash.
    area: Roles-screen area, or ADMIN.
    verbs: {METHOD: action} overrides (None = the table above).
    extra: list of (area, action) also required, for any method in `extra_on`.
    """
    return (re.compile(pattern), area, verbs or {}, extra or {})


# Order matters: specific before general.
RULES = [
    # ── administration: no tick, admin only ──────────────────────────────────
    _rule(r'^/accounts/workspace/(members|roles|sessions|activity)(/.*)?$', ADMIN),
    _rule(r'^/white-label(?!/config)(/.*)?$', ADMIN,
          verbs={'GET': None, 'HEAD': None, 'OPTIONS': None}),

    # ── reporting ────────────────────────────────────────────────────────────
    _rule(r'^/analytics/calls/export$', 'reporting', {'GET': 'download'}),
    _rule(r'^/analytics/calls/[^/]+/recording$', 'reporting', {'GET': 'listen'}),
    _rule(r'^/analytics/reports(/.*)?$', 'reporting', {'DELETE': 'edit'}),
    _rule(r'^/analytics(/.*)?$', 'reporting', {'POST': 'view'}),
    _rule(r'^/stats(/.*)?$', 'reporting'),
    # Live Monitor and AI Insights read the same data the reports do.
    _rule(r'^/ai/(recommendations|anomalies)$', 'reporting'),
    _rule(r'^/ai/autopilot(/.*)?$', 'campaigns',
          {'GET': 'view', 'POST': 'edit-advanced', 'PATCH': 'edit-advanced', 'PUT': 'edit-advanced'}),

    # ── campaigns ────────────────────────────────────────────────────────────
    _rule(r'^/campaigns/[^/]+/(cap|schedules)$', 'campaigns',
          {'PATCH': 'edit-advanced', 'PUT': 'edit-advanced', 'POST': 'edit-advanced'}),
    _rule(r'^/campaigns/[^/]+/(pause|activate)$', 'campaigns', {'POST': 'edit-campaign'}),
    _rule(r'^/campaigns(/[^/]+)?$', 'campaigns',
          {'POST': 'edit-campaign', 'PATCH': 'edit-campaign', 'PUT': 'edit-campaign', 'DELETE': 'delete'}),
    _rule(r'^/campaigns(/.*)?$', 'campaigns', {'GET': 'view', 'POST': 'edit-campaign', 'PATCH': 'edit-campaign'}),

    # ── call flows (routing rules, IVR, queue) ───────────────────────────────
    # Where a rule forwards calls to is "Edit Forward Calls To" on a campaign.
    _rule(r'^/routing/rules/[^/]+/destinations(/.*)?$', 'call-flows',
          extra={'POST': [('campaigns', 'edit-forward')],
                 'PATCH': [('campaigns', 'edit-forward')],
                 'PUT': [('campaigns', 'edit-forward')],
                 'DELETE': [('campaigns', 'edit-forward')]}),
    # The LIVE column on a campaign's Routed Destinations and Tracking Numbers
    # (CH-211): anyone who may see the campaign may see its live counts.
    _rule(r'^/routing/calls/live-counts$', 'campaigns', {'GET': 'view'}),
    _rule(r'^/routing(/.*)?$', 'call-flows'),
    _rule(r'^/ivr/(?!webhook)(.*)$', 'call-flows'),
    _rule(r'^/queue(/.*)?$', 'call-flows'),

    # ── tracking numbers and pools ───────────────────────────────────────────
    # Attaching a number to a campaign is "Edit Tracking Numbers" on the campaign.
    _rule(r'^/numbers/[^/]+/assign$', 'tracking-numbers',
          extra={'POST': [('campaigns', 'edit-numbers')]}),
    # Testing a number (CH-175) is a question, not a change: anyone who may see
    # the number may ask whether it is alive. Rate-limited in the handler.
    _rule(r'^/numbers/[^/]+/test$', 'tracking-numbers', verbs={'POST': 'view'}),
    # TFN Check (CH-213): checking numbers is a question too. Making and
    # cancelling share links are the usual Edit and Delete.
    _rule(r'^/numbers/check$', 'tracking-numbers', verbs={'POST': 'view'}),
    _rule(r'^/numbers(/.*)?$', 'tracking-numbers'),
    _rule(r'^/dni/(?!snippet|assign)(.*)$', 'number-pools'),

    # ── destinations, buyers, traffic sources, vendors ───────────────────────
    _rule(r'^/destinations(/.*)?$', 'destinations'),
    # Who may log in as this buyer is administration, not a Buyers tick.
    _rule(r'^/buyers/[^/]+/access$', ADMIN),
    _rule(r'^/buyers(/.*)?$', 'buyers'),
    _rule(r'^/publishers(/.*)?$', 'traffic-sources'),
    # A vendor is the carrier a number is bought from. Reading the list is also
    # allowed with Tracking Numbers View, since the numbers screens need it.
    _rule(r'^/carriers(/.*)?$', 'vendors', {'GET': None}),

    # ── integrations, webhooks, marketplace, alert rules ─────────────────────
    _rule(r'^/integrations(/.*)?$', 'integrations'),
    _rule(r'^/webhooks/(?!conversion)(.*)$', 'integrations'),
    _rule(r'^/webhooks$', 'integrations'),
    _rule(r'^/marketplace/services/[^/]+/favorite$', 'integrations',
          {'POST': 'view', 'DELETE': 'view'}),
    # Reading the directory needs Integrations View. Adding or editing listings
    # is platform staff work and is decided in the handler.
    _rule(r'^/marketplace(/.*)?$', 'integrations',
          {'POST': None, 'PATCH': None, 'PUT': None, 'DELETE': None}),
    _rule(r'^/notifications/(rules|logs|test)(/.*)?$', 'integrations'),

    # ── block lists ──────────────────────────────────────────────────────────
    # Shields are decided per type (VoIP or TCPA) inside the handler.
    _rule(r'^/spam/shields(/.*)?$', None),
    # Which callers are blocked, to colour them red in any call log (CH-185).
    # Every login that can see a call log needs it; it says nothing else.
    _rule(r'^/spam/blocked-callers$', None),
    _rule(r'^/spam(/.*)?$', 'blocked-numbers'),

    # ── billing ──────────────────────────────────────────────────────────────
    _rule(r'^/billing/(?!.*webhook)(.*)$', 'billing', {'DELETE': 'edit'}),

    # ── RTB bid log ──────────────────────────────────────────────────────────
    _rule(r'^/rtb/auctions(/.*)?$', 'rtb-bid-log', {'POST': 'view'}),
]

def requirements(method: str, path: str):
    """The (area, action) pairs this request needs, [] when it needs none, or
    the string ADMIN. `path` is the path after /api, any trailing slash allowed.
    """
    method = method.upper()
    clean = '/' + path.strip('/')
    for pattern, area, verbs, extra in RULES:
        if not pattern.match(clean):
            continue
        if area is None:
            return []
        if area == ADMIN:
            # white-label reads stay open (the sidebar and branding use them)
            if method in verbs and verbs[method] is None:
                return []
            return ADMIN
        action = verbs.get(method, VERBS.get(method))
        if action is None:
            return []
        out = [(area, action)]
        out.extend(extra.get(method, []))
        return out
    return []


def _bypass(user) -> bool:
    role = getattr(user, 'role', '')
    return (getattr(user, 'is_superuser', False)
            or role in ('admin', 'reseller', 'buyer', 'publisher'))


def enforce_route(request, user):
    """Raise 403 unless this user's role holds every tick the route needs.

    Called by `JWTAuth` right after the token is accepted. Never limits an
    admin, a reseller, a superuser or a partner login.
    """
    if _bypass(user):
        return
    need = requirements(request.method, request.path_info.removeprefix('/api'))
    if not need:
        return
    if need == ADMIN:
        from accounts.permissions import Capability, require
        require(user, Capability.MEMBERS)
        return
    from accounts.workspace_permissions import effective_permissions
    held = getattr(request, '_workspace_perms', None)
    if held is None:
        held = set(effective_permissions(user))
        request._workspace_perms = held
    for area, action in need:
        if f'{area}.{action}' not in held:
            raise HttpError(403, f"Your role doesn't have access to {area.replace('-', ' ')} ({action}).")
