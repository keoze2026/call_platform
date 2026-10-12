"""The 55 workspace permissions: catalogue, defaults, resolution, enforcement.

The Roles screen offered 55 ticks in 21 areas while the server knew ten broad
rights, so "no Reports for Manager" could not even be expressed - the admin
unticked Reporting, saved, and the Manager kept seeing Reports (the owner's
test, verbatim). This module is the missing middle: the catalogue the screen
renders, the defaults each built-in role seeds with, and `require_perm`,
which is the 403 that actually protects the data.

Partners (buyer / publisher logins) are explicitly NOT governed here - their
Partner Access rules in `partner_permissions.py` stay exactly as they are.
"""
from ninja.errors import HttpError

# ── catalogue ────────────────────────────────────────────────────────────────
# area -> actions. Dotted keys everywhere: "reporting.view".

CATALOGUE = {
    'reporting':         ['view', 'edit', 'listen', 'download'],
    'campaigns':         ['view', 'edit-campaign', 'edit-numbers', 'edit-forward', 'edit-advanced', 'delete'],
    'call-flows':        ['view', 'edit', 'delete'],
    'tracking-numbers':  ['view', 'edit', 'delete'],
    'number-pools':      ['view', 'edit', 'delete'],
    'destinations':      ['view', 'edit', 'delete'],
    'groups':            ['view', 'edit', 'delete'],
    'ping-post-log':     ['view'],
    'rtb-bid-log':       ['view'],
    'buyers':            ['view', 'edit', 'delete'],
    'vendors':           ['view', 'edit', 'delete'],
    'traffic-sources':   ['view', 'edit', 'delete'],
    'integrations':      ['view', 'edit', 'delete'],
    'leads':             ['view', 'delete'],
    'custom-parameters': ['view', 'edit', 'delete'],
    'voip-shield':       ['view', 'edit', 'delete'],
    'dispute':           ['view'],
    'tcpa-shield':       ['view'],
    'billing':           ['view', 'edit'],
    'blocked-numbers':   ['view', 'edit', 'delete'],
    'global-offers':     ['view'],
}

ALL_KEYS = sorted(f'{area}.{action}' for area, actions in CATALOGUE.items() for action in actions)

BUILTIN_SLUGS = ('admin', 'manager', 'viewer', 'agent')


def _manager_defaults():
    keys = set()
    keys |= {f'reporting.{a}' for a in CATALOGUE['reporting']}
    keys |= {'campaigns.view', 'campaigns.edit-campaign', 'campaigns.edit-forward', 'campaigns.edit-advanced'}
    for area in ('call-flows', 'tracking-numbers', 'number-pools', 'destinations',
                 'groups', 'buyers', 'vendors', 'traffic-sources', 'integrations',
                 'custom-parameters', 'voip-shield', 'blocked-numbers'):
        keys |= {f'{area}.view', f'{area}.edit'}
    keys |= {'ping-post-log.view', 'leads.view', 'billing.view'}
    return sorted(keys)


def _viewer_defaults():
    return sorted(f'{area}.view' for area in CATALOGUE)


def _agent_defaults():
    # Today's behaviour on ticks: views plus edits, never deletes.
    keys = set(_viewer_defaults())
    for area, actions in CATALOGUE.items():
        for action in actions:
            if action.startswith('edit'):
                keys.add(f'{area}.{action}')
    keys |= {'reporting.listen', 'reporting.download'}
    return sorted(keys)


BUILTIN_DEFAULTS = {
    'admin': list(ALL_KEYS),
    'manager': _manager_defaults(),
    'viewer': _viewer_defaults(),
    'agent': _agent_defaults(),
}

BUILTIN_NAMES = {'admin': 'Admin', 'manager': 'Manager', 'viewer': 'Viewer', 'agent': 'Agent'}
BUILTIN_DESCRIPTIONS = {
    'admin':   'Full access. Can manage members and billing. Locked.',
    'manager': 'Runs the operation. No billing changes, no member management.',
    'viewer':  'Read-only everywhere.',
    'agent':   'Works the queue: views and edits, never deletes.',
}


def clean_keys(raw) -> list:
    """Known dotted keys only, deduplicated, stable order. Unknown keys drop."""
    if not isinstance(raw, (list, tuple)):
        return []
    allowed = set(ALL_KEYS)
    return sorted({str(k) for k in raw if str(k) in allowed})


# ── storage ──────────────────────────────────────────────────────────────────

def ensure_builtin_roles(organization):
    """Each workspace carries its own editable copy of the built-ins, seeded
    from the defaults the first time anyone looks. Admin's row exists for the
    display but is locked by the PATCH endpoint, so a workspace can never
    lock itself out."""
    from django.db import IntegrityError
    from accounts.models import CustomRole
    for slug in BUILTIN_SLUGS:
        if CustomRole.objects.filter(organization=organization, slug=slug).exists():
            continue
        # The table is unique on (organization, name), and this workspace
        # already held a custom role literally named "Admin" from an earlier
        # build - creating alongside it collided and the crash took the whole
        # seeding (and every acceptance test after it) down. A same-named
        # legacy row is adopted as the built-in instead of fought with.
        legacy = CustomRole.objects.filter(
            organization=organization, slug__isnull=True,
            name__iexact=BUILTIN_NAMES[slug],
        ).first()
        if legacy is not None:
            legacy.slug = slug
            legacy.is_builtin = True
            if not legacy.capabilities:
                legacy.capabilities = BUILTIN_DEFAULTS[slug]
            legacy.save()
            continue
        try:
            CustomRole.objects.create(
                organization=organization, slug=slug,
                name=BUILTIN_NAMES[slug],
                description=BUILTIN_DESCRIPTIONS[slug],
                capabilities=BUILTIN_DEFAULTS[slug],
                is_builtin=True,
            )
        except IntegrityError:
            # Raced or renamed oddly - never let seeding kill the caller.
            continue


# ── resolution ───────────────────────────────────────────────────────────────

def effective_permissions(user) -> list:
    """The dotted keys this login holds. Admin and the owner hold all.
    Partners are not governed here and get []."""
    role = getattr(user, 'role', '')
    if getattr(user, 'is_superuser', False) or role in ('admin', 'reseller'):
        return list(ALL_KEYS)
    if role in ('buyer', 'publisher'):
        return []
    org = getattr(user, 'organization', None)
    if org is None:
        return []
    from accounts.models import CustomRole
    row = (CustomRole.objects.filter(organization=org, slug=role).first()
           or CustomRole.objects.filter(organization=org, id__iexact=role).first()
           if _looks_like_uuid(role) else
           CustomRole.objects.filter(organization=org, slug=role).first())
    if row is None:
        # An employee role with no row yet: seed and read the default.
        if role in BUILTIN_SLUGS:
            ensure_builtin_roles(org)
            row = CustomRole.objects.filter(organization=org, slug=role).first()
    return clean_keys(row.capabilities) if row else []


def _looks_like_uuid(value: str) -> bool:
    import uuid as _uuid
    try:
        _uuid.UUID(str(value))
        return True
    except Exception:
        return False


def has_perm(user, area: str, action: str) -> bool:
    role = getattr(user, 'role', '')
    if getattr(user, 'is_superuser', False) or role in ('admin', 'reseller'):
        return True
    if role in ('buyer', 'publisher'):
        # Partner Access governs partners; this system never blocks them.
        return True
    return f'{area}.{action}' in set(effective_permissions(user))


def require_perm(user, area: str, action: str):
    """The 403 that actually protects an area. Hidden menus are a courtesy;
    this is the permission."""
    if has_perm(user, area, action):
        return
    raise HttpError(403, f"Your role doesn't have access to {area.replace('-', ' ')} ({action}).")
