"""The workspace's main user: the only login that may see or manage API keys
(CH-243).

Sir, 24 Oct: "APIs are very sensitive info - only visible and editable in the
main account by the main login user. Not allowed anyone: publisher, buyer,
admin, manager - just the main user of that account." Every login, a publisher
or buyer included, could list every key of the workspace, make keys and revoke
anyone's - a partner could switch off the keys our buyer bots run on.

The main user is the workspace's first admin: the account that signed the
workspace up (the earliest active admin, by when it joined). The platform owner
(superuser) also may, as for every other workspace setting. Keys already in use
keep working - this governs who sees and manages them, not who calls the API.
"""
from ninja.errors import HttpError


def main_user_of(organization):
    if organization is None:
        return None
    from accounts.models import User
    return (User.objects.filter(organization=organization, role='admin', is_active=True)
            .order_by('date_joined', 'id').first())


def is_main_user(user) -> bool:
    if user is None:
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if getattr(user, 'role', '') != 'admin':
        return False
    main = main_user_of(getattr(user, 'organization', None))
    return main is not None and main.pk == user.pk


def require_main_user(user) -> None:
    if not is_main_user(user):
        raise HttpError(403, 'Only the main account of this workspace can see or manage API keys.')
