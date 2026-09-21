"""Outgoing account email: invitations and password resets.

Both flows previously sent nothing. The invite endpoint created a user and
returned a temporary password in the API response, and the reset flow printed a
link to the console against a placeholder domain, so neither ever reached an
inbox.

send_account_email returns (sent, error) rather than raising, so a caller can
report delivery status instead of failing the request over SMTP.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _from_email() -> str:
    return getattr(settings, 'INVITE_FROM_EMAIL', None) or settings.DEFAULT_FROM_EMAIL


def _frontend_url() -> str:
    return getattr(settings, 'FRONTEND_URL', 'https://avortyx.io').rstrip('/')


def send_account_email(to_email: str, subject: str, body: str):
    """Send one account email. Returns (sent: bool, error: str | None)."""
    try:
        delivered = send_mail(
            subject=subject,
            message=body,
            from_email=_from_email(),
            recipient_list=[to_email],
            fail_silently=False,
        )
        if delivered:
            return True, None
        # send_mail returning 0 means the backend accepted nothing
        logger.warning('account email not delivered to %s: %s', to_email, subject)
        return False, 'Mail server accepted no recipients'
    except Exception as exc:
        logger.exception('account email failed to %s: %s', to_email, subject)
        return False, str(exc)


def send_invite_email(user, organization, reset_token: str, invited_by=None):
    """Invite a new member with a link to set their own password.

    A set-password link rather than the generated password itself: the password
    is already in the API response for whoever sent the invite, and a link can
    expire.
    """
    link = f"{_frontend_url()}/reset-password?token={reset_token}"
    inviter = ''
    if invited_by is not None:
        name = (invited_by.get_full_name() or '').strip() or invited_by.email
        inviter = f"{name} has invited you"
    else:
        inviter = "You have been invited"

    body = (
        f"Hi,\n\n"
        f"{inviter} to join {organization.name} on Avortyx.\n\n"
        f"Set your password to activate your account:\n"
        f"{link}\n\n"
        f"This link expires in 24 hours. If it expires, use Forgot Password on "
        f"the sign-in page to request a new one.\n\n"
        f"Sign in at: {_frontend_url()}\n"
        f"Your username is your email address: {user.email}\n\n"
        f"Avortyx Team"
    )
    return send_account_email(
        user.email, f"You have been invited to {organization.name} on Avortyx", body
    )


def send_password_reset_email(user, reset_token: str):
    link = f"{_frontend_url()}/reset-password?token={reset_token}"
    body = (
        f"Hi,\n\n"
        f"A password reset was requested for this account.\n\n"
        f"Reset your password:\n{link}\n\n"
        f"This link expires in 24 hours. If you did not request this, you can "
        f"ignore this email and your password will stay unchanged.\n\n"
        f"Avortyx Team"
    )
    return send_account_email(user.email, "Reset your Avortyx password", body)
