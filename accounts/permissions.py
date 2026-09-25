"""What each role may do, in one place.

Nothing checked roles before: any account inside a workspace could reach every
endpoint, including billing and member management. The five roles existed as
labels only.

Two kinds of restriction, and they are different problems:

  capability  what a role may DO      - a manager cannot open billing
  scope       what a role may SEE     - a buyer sees only their own calls

Capability is a gate on the endpoint. Scope has to filter rows, because a buyer
login is inside the organization and its data sits beside everyone else's.

The rules live here rather than being spread across endpoints so the answer to
"what can an agent do" is one file, not a search.
"""
from ninja.errors import HttpError


class Capability:
    """Things a role can be allowed to do."""

    VIEW = 'view'                    # read calls, reports, dashboards
    EDIT = 'edit'                    # change existing campaigns, buyers, numbers
    CREATE = 'create'                # add campaigns, buyers, publishers, numbers
    DELETE = 'delete'                # remove them
    BILLING = 'billing'              # balance, rates, payment methods, invoices
    MEMBERS = 'members'              # invite, remove, change roles
    SETTINGS = 'settings'            # workspace settings, integrations, white label


# Role → what it may do. A role absent from here gets VIEW only, so a new role
# added to the model is harmless until it is given capabilities deliberately.
ROLE_CAPABILITIES = {
    'admin': {
        Capability.VIEW, Capability.EDIT, Capability.CREATE, Capability.DELETE,
        Capability.BILLING, Capability.MEMBERS, Capability.SETTINGS,
    },
    'reseller': {
        Capability.VIEW, Capability.EDIT, Capability.CREATE, Capability.DELETE,
        Capability.BILLING, Capability.MEMBERS, Capability.SETTINGS,
    },
    # Runs the operation, but money and people are the admin's
    'manager': {
        Capability.VIEW, Capability.EDIT, Capability.CREATE, Capability.DELETE,
    },
    # Works the queue: can change what exists, cannot add or remove
    'agent': {
        Capability.VIEW, Capability.EDIT,
    },
    # Outside parties with a login. They see their own calls and nothing else.
    'buyer': {Capability.VIEW},
    'publisher': {Capability.VIEW},
}

# Roles whose view is limited to their own records rather than the whole
# organization. Everyone else sees the organization's data.
SCOPED_ROLES = {'buyer', 'publisher'}


def capabilities_for(user) -> set:
    if user is None:
        return set()
    if getattr(user, 'is_superuser', False):
        return set(ROLE_CAPABILITIES['admin'])
    return ROLE_CAPABILITIES.get(getattr(user, 'role', ''), {Capability.VIEW})


def has(user, capability: str) -> bool:
    return capability in capabilities_for(user)


def require(user, capability: str):
    """Raise 403 unless the user holds the capability.

    The message names the capability and the role, so a blocked request explains
    itself instead of returning a bare Forbidden.
    """
    if has(user, capability):
        return
    raise HttpError(
        403,
        f"Your role ({getattr(user, 'role', 'unknown')}) cannot {capability}. "
        f"Ask an admin of this workspace."
    )


def is_scoped(user) -> bool:
    """True when this user should only see their own records."""
    return getattr(user, 'role', '') in SCOPED_ROLES


def scope_queryset(user, qs, buyer_field='buyer', publisher_field='publisher'):
    """Narrow a queryset to what this user is allowed to see.

    Unscoped roles get the queryset untouched. A scoped role with no linked
    record gets nothing: a buyer login that was never tied to a buyer must not
    fall through to seeing the whole organization.

    Pass None for a field the model does not have, so the same call works across
    call records, campaigns and reports.
    """
    # A superuser is never scoped. Support has to see the whole workspace, and a
    # stray role on a superuser account must not blank out their dashboard.
    if getattr(user, 'is_superuser', False):
        return qs

    role = getattr(user, 'role', '')

    if role == 'buyer':
        if buyer_field is None or not getattr(user, 'buyer_id', None):
            return qs.none()
        return qs.filter(**{buyer_field: user.buyer_id})

    if role == 'publisher':
        if publisher_field is None or not getattr(user, 'publisher_id', None):
            return qs.none()
        return qs.filter(**{publisher_field: user.publisher_id})

    return qs
