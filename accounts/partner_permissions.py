"""What a buyer or publisher login may do, and which report columns it sees.

The publisher settings page offers five permission toggles and eight reporting
checkboxes. None of them had a backend: the page saved them to the browser, so
they came back after a refresh on that machine and meant nothing anywhere else.
Two people looking at the same publisher saw different settings, and the toggles
gated nothing at all.

This is the backend for them, built on the capability system in
`accounts/permissions.py` rather than beside it. A toggle here turns into a real
capability, which `require()` already enforces on every endpoint that calls it.
Nothing has to remember to check a second system.

Three things kept deliberately in one place:

  the catalogue      the keys, labels and descriptions the interface renders.
                     The frontend hardcoded its own list, which is how a toggle
                     ends up existing in the UI with nothing behind it. It can
                     now read them from `/api/accounts/partner-permissions`.

  the defaults       what a partner gets when nobody has decided. Read-only and
                     their own reports - the safe end, because a partner login
                     belongs to another company.

  the mapping        which capability each toggle grants. One place to look when
                     asking why a partner could or could not do something.
"""

# ── permissions ──────────────────────────────────────────────────────────────

MANAGE_TRAFFIC = 'manage_traffic'
NUMBER_CREATION = 'number_creation'
AUDIO_RECORDING = 'audio_recording'
BLOCK_NUMBERS = 'block_numbers'
DOWNLOAD_REPORTS = 'download_reports'

PERMISSIONS = [
    {
        'key': MANAGE_TRAFFIC,
        'label': 'Manage Traffic',
        'description': 'Users can effectively manage and optimize traffic.',
        'default': False,
    },
    {
        'key': NUMBER_CREATION,
        'label': 'Number Creation',
        'description': 'Users can purchase a new number within the system.',
        'default': False,
    },
    {
        'key': AUDIO_RECORDING,
        'label': 'Audio Recording',
        'description': 'It provides users with the ability to view call recordings.',
        'default': False,
    },
    {
        'key': BLOCK_NUMBERS,
        'label': 'Block Numbers',
        'description': 'Reject calls from specific phone numbers.',
        'default': False,
    },
    {
        'key': DOWNLOAD_REPORTS,
        'label': 'Download Reports',
        'description': 'Users can download reports about their call activity.',
        'default': True,
    },
]

PERMISSION_KEYS = {p['key'] for p in PERMISSIONS}


# ── report columns ───────────────────────────────────────────────────────────
# What a partner may see of their own numbers. Payout and revenue are separated
# on purpose: a publisher is paid a payout and has no business seeing what the
# call was sold for, and showing both is how a partner works out the margin.

REPORT_COLUMNS = [
    {'key': 'incoming',   'label': 'Incoming',   'description': 'Total inbound call attempts.',       'default': True},
    {'key': 'connected',  'label': 'Connected',  'description': 'Calls that reached a destination.',  'default': True},
    {'key': 'qualified',  'label': 'Qualified',  'description': "Calls that met the buyer's quality criteria.", 'default': True},
    {'key': 'converted',  'label': 'Converted',  'description': 'Calls that converted into a paid event.', 'default': True},
    {'key': 'duplicate',  'label': 'Duplicate',  'description': 'Repeat callers inside the duplicate window.', 'default': True},
    {'key': 'duration',   'label': 'Duration',   'description': 'Time connected, excluding ring time.', 'default': True},
    {'key': 'payout',     'label': 'Payout',     'description': 'What this partner is paid.',         'default': True},
    {'key': 'revenue',    'label': 'Revenue',    'description': 'What the call was sold for.',        'default': False},
]

REPORT_COLUMN_KEYS = {c['key'] for c in REPORT_COLUMNS}


# ── defaults ─────────────────────────────────────────────────────────────────

def default_permissions() -> dict:
    return {p['key']: p['default'] for p in PERMISSIONS}


def default_report_columns() -> list:
    return [c['key'] for c in REPORT_COLUMNS if c['default']]


def clean_permissions(raw) -> dict:
    """Keep only keys we know, as real booleans.

    An unknown key is dropped rather than stored. A permission that exists in
    the database and nowhere in the code is the same problem as a toggle in the
    UI with no backend, in the other direction.
    """
    if not isinstance(raw, dict):
        return default_permissions()
    out = default_permissions()
    for key, value in raw.items():
        if key in PERMISSION_KEYS:
            out[key] = bool(value)
    return out


def clean_report_columns(raw) -> list:
    """Keep only columns we know, in the order the catalogue defines.

    Ordered by the catalogue rather than by what arrived, so two partners with
    the same columns always render them the same way round.
    """
    if not isinstance(raw, (list, tuple, set)):
        return default_report_columns()
    chosen = {str(k) for k in raw}
    return [c['key'] for c in REPORT_COLUMNS if c['key'] in chosen]


# ── what a toggle actually grants ────────────────────────────────────────────
# The left side is a toggle on the settings page; the right side is a capability
# `require()` already enforces. This is the whole mapping - if a partner could
# do something they should not, the answer is here.

from accounts.permissions import Capability  # noqa: E402  (avoids a cycle at import time)

PERMISSION_CAPABILITIES = {
    MANAGE_TRAFFIC: {Capability.EDIT},
    NUMBER_CREATION: {Capability.CREATE},
    AUDIO_RECORDING: {Capability.RECORDINGS},
    BLOCK_NUMBERS: {Capability.BLOCK_NUMBERS},
    DOWNLOAD_REPORTS: {Capability.EXPORT},
}


def partner_record(user):
    """The buyer or publisher this login belongs to, or None.

    A partner login with no record attached sees nothing, which is the correct
    answer: the scoping has nothing to scope it to.
    """
    if user is None:
        return None
    role = getattr(user, 'role', '')
    if role == 'publisher':
        return getattr(user, 'publisher', None)
    if role == 'buyer':
        return getattr(user, 'buyer', None)
    return None


def capabilities_for_partner(user) -> set:
    """What this partner login may do, from the toggles on its own record."""
    from accounts.permissions import Capability

    record = partner_record(user)
    # Always read-only at minimum, and only ever of their own rows - the row
    # scoping in `accounts/permissions.py` is what limits that, not this.
    granted = {Capability.VIEW}
    if record is None:
        return granted

    perms = clean_permissions(getattr(record, 'permissions', None))
    for key, enabled in perms.items():
        if enabled:
            granted |= PERMISSION_CAPABILITIES.get(key, set())
    return granted


def visible_report_columns(user) -> list:
    """The report columns this login may see. Everything, for staff."""
    record = partner_record(user)
    if record is None:
        return [c['key'] for c in REPORT_COLUMNS]
    return clean_report_columns(getattr(record, 'visible_report_columns', None))


def catalogue() -> dict:
    """What the interface should render, so it stops keeping its own list."""
    return {'permissions': PERMISSIONS, 'report_columns': REPORT_COLUMNS}
