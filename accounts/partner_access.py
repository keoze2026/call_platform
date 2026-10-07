"""Revoke a partner login, and the pieces every caller of that needs.

Deleting a buyer or publisher only archived the partner. The login account was
left exactly as it was - same password, still active, its sessions still valid -
so the person kept full access to a workspace they had been removed from. And
inviting the same address again reused that account as it stood, which is why a
brand-new invitation showed as "Registered".

Both problems are the same missing step: nothing ever revoked the login. This
is that step, in one place, so delete, re-invite and remove-member cannot drift
apart.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

PARTNER_ROLES = ('buyer', 'publisher')


def is_partner_account(user) -> bool:
    """A login that belongs to a buyer or publisher, not a member of staff.

    Staff are never reset. An admin or manager who happens to share an address
    with a partner would otherwise be locked out of their own workspace by
    someone else's invitation.
    """
    from .models import User
    return (
        user.role in PARTNER_ROLES
        or user.invite_status == User.InviteStatus.REVOKED
    )


def end_all_sessions(user) -> int:
    """Sign the account out everywhere by blacklisting its refresh tokens.

    Access tokens stay valid until they expire - 60 minutes - which is inherent
    to JWT. Blacklisting stops them being renewed, so access ends within the
    hour rather than never.
    """
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken, OutstandingToken,
        )
    except Exception:
        logger.warning('token blacklist is not installed; %s stays signed in', user.email)
        return 0

    count = 0
    for token in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        if created:
            count += 1
    return count


def revoke_partner_access(user, *, reason: str = '') -> bool:
    """Take a partner's access away completely. Returns False for staff.

    Does all four things that have to happen together: the password stops
    working, the sessions end, any unused invitation link dies, and the status
    says so. Doing three of the four leaves a way back in.
    """
    from accounts.access_requests import SetupToken
    from .models import User

    if not is_partner_account(user):
        logger.info('not revoking %s: role is %s, not a partner', user.email, user.role)
        return False

    ended = end_all_sessions(user)
    user.set_unusable_password()
    user.buyer = None
    user.publisher = None
    user.last_login = None
    user.invite_status = User.InviteStatus.REVOKED
    user.accepted_at = None
    user.save(update_fields=[
        'password', 'buyer', 'publisher', 'last_login',
        'invite_status', 'accepted_at',
    ])

    SetupToken.objects.filter(user=user, is_used=False).update(is_used=True)

    logger.info(
        'revoked partner access for %s (%s sessions ended)%s',
        user.email, ended, f' - {reason}' if reason else '',
    )
    return True


def revoke_partner_logins(partner, kind: str, *, reason: str = '') -> int:
    """Revoke every login attached to one buyer or publisher."""
    from .models import User

    field = 'buyer_id' if kind == 'buyer' else 'publisher_id'
    users = User.objects.filter(**{field: partner.id}, role__in=PARTNER_ROLES)
    revoked = 0
    for user in users:
        if revoke_partner_access(user, reason=reason):
            revoked += 1
    return revoked


def reset_for_new_invite(user, *, kind: str, partner, organization) -> None:
    """Make an existing partner account a brand-new invitation.

    Everything the old invitation granted has to stop: the password, the open
    sessions, and any link from the previous email. Without this the person is
    simply still signed in, which is exactly what was reported.
    """
    from accounts.access_requests import SetupToken
    from .models import User

    end_all_sessions(user)
    user.set_unusable_password()
    user.last_login = None
    user.is_active = True
    user.invite_status = User.InviteStatus.INVITED
    user.invited_at = timezone.now()
    user.accepted_at = None
    SetupToken.objects.filter(user=user, is_used=False).update(is_used=True)
    logger.info('reset %s for a fresh %s invitation', user.email, kind)
