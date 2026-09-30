"""Invite a buyer or publisher to their own login.

The interface offered "Invite a publisher" and "Invite a buyer". Neither worked.

There was no publisher invite endpoint at all — the dialog had nothing behind it.
The buyer one existed but was broken three ways: it emailed
`buyer.created_by.email`, which is the admin who created the record rather than
the buyer; it generated a token, put it in the link, and never stored it, so the
link could never be validated; and it ignored the email address the form
collected.

This builds both on the token flow that already works for staff accounts —
`SetupToken` plus `/api/accounts/set-password/`, which validates the token, sets
the password and returns a login. Nothing new to maintain.

The account created is linked to the buyer or publisher record through
`User.buyer` / `User.publisher`, so the row-level scoping added with the roles
work applies: the partner signs in and sees only their own calls.
"""
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from accounts.access_requests import SetupToken
from accounts.models import User

logger = logging.getLogger(__name__)

INVITE_VALID_HOURS = 48


class InviteError(Exception):
    """Something the caller can fix, with a message worth showing them."""


def _clean_email(raw: str) -> str:
    email = (raw or '').strip().lower()
    if not email:
        raise InviteError("An email address is required to send the invitation.")
    try:
        validate_email(email)
    except DjangoValidationError:
        raise InviteError(f"'{raw}' is not a valid email address.")
    return email


@transaction.atomic
def invite_partner(*, organization, partner, kind: str, email: str,
                   contact_name: str = '', invited_by=None) -> dict:
    """Create or reuse the partner's login and send them a setup link.

    `kind` is 'buyer' or 'publisher'. The link goes to the address given on the
    form, which is the partner's own — not whoever created the record.
    """
    if kind not in ('buyer', 'publisher'):
        raise InviteError(f"Unknown partner type {kind!r}.")

    email = _clean_email(email)
    name = (contact_name or getattr(partner, 'name', '') or '').strip()

    user = User.objects.filter(email__iexact=email).first()

    if user is None:
        user = User(
            email=email,
            username=email,
            first_name=name[:150],
            role=kind,
            organization=organization,
            is_active=True,
        )
        # No usable password until they set one through the link.
        user.set_unusable_password()
        user.save()
        created = True
    else:
        if user.organization_id and user.organization_id != organization.id:
            raise InviteError(
                f"{email} already belongs to another workspace and cannot be "
                f"invited here."
            )
        created = False

    # The link that makes the scoping work: without it the login has the role but
    # no way to know which buyer or publisher it is, and sees nothing.
    setattr(user, f'{kind}_id', partner.id)
    if not user.organization_id:
        user.organization = organization

    # A staff account keeps its role - narrowing an admin to the partner view
    # would lock them out of their own workspace. Only a partner login, or a
    # brand new one, gets the buyer or publisher role.
    if created or user.role in ('buyer', 'publisher'):
        user.role = kind
    else:
        logger.info(
            'invite: %s is already %r in this workspace, leaving the role alone',
            email, user.role,
        )

    user.save()

    # Any earlier unused link is invalidated, so only the newest one works.
    SetupToken.objects.filter(user=user, is_used=False).update(is_used=True)

    token_str = secrets.token_urlsafe(32)
    SetupToken.objects.create(
        user=user,
        token=token_str,
        expires_at=timezone.now() + timedelta(hours=INVITE_VALID_HOURS),
    )

    setup_link = f"{settings.FRONTEND_URL}/set-password?token={token_str}"

    sent = _send(email, name, kind, partner, setup_link)

    return {
        'success': True,
        'message': (
            f"Invitation sent to {email}."
            if sent else
            f"Invitation created for {email}, but the email could not be sent. "
            f"Share the link below directly."
        ),
        'email': email,
        'email_sent': sent,
        'setup_link': setup_link,
        'expires_in_hours': INVITE_VALID_HOURS,
        'user_id': str(user.id),
        'account_created': created,
    }


def _send(email: str, name: str, kind: str, partner, setup_link: str) -> bool:
    """Send the invitation. Returns whether it went.

    Not silent: the caller reports the outcome, and the link is returned either
    way so an invitation is never lost to a mail problem.
    """
    from django.core.mail import send_mail

    greeting = f"Hi {name}," if name else "Hi,"
    label = 'buyer' if kind == 'buyer' else 'publisher'

    try:
        send_mail(
            subject=f"You have been invited to Avortyx as a {label}",
            message=(
                f"{greeting}\n\n"
                f"You have been invited to Avortyx as a {label} for "
                f"{getattr(partner, 'name', '')}.\n\n"
                f"Set your password and sign in here:\n\n{setup_link}\n\n"
                f"This link expires in {INVITE_VALID_HOURS} hours.\n\n"
                f"Avortyx Team"
            ),
            from_email=settings.PLATFORM_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception('partner invite email failed for %s', email)
        return False
