"""Reports PIN: set it, verify it, and say whether a session is unlocked."""
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from .models import ReportsPin, ReportsPinAttempt, ReportsPinUnlock

logger = logging.getLogger(__name__)

PIN_RE = re.compile(r'^\d{4}$')


def unlock_minutes() -> int:
    return getattr(settings, 'REPORTS_PIN_UNLOCK_MINUTES', 30)


def max_attempts() -> int:
    return getattr(settings, 'REPORTS_PIN_MAX_ATTEMPTS', 5)


def lockout_minutes() -> int:
    return getattr(settings, 'REPORTS_PIN_LOCKOUT_MINUTES', 5)


class PinError(Exception):
    """Carries the code and status the API should answer with."""

    def __init__(self, code: str, detail: str, status: int = 400, **extra):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.extra = extra


def get_pin(organization):
    return ReportsPin.objects.filter(organization=organization).first()


def session_key(request) -> str:
    """Which login this is.

    Prefer the refresh token's jti so a second device starts locked. SimpleJWT
    puts the claims on the validated token; when it is not there we fall back to
    the user id, and the 30-minute expiry is then the only limit. Falling back
    is deliberate: refusing to unlock at all because a claim is missing would
    lock people out of their own data.
    """
    token = getattr(request, 'auth', None)
    for attr in ('token', '_token', 'payload'):
        claims = getattr(token, attr, None)
        if isinstance(claims, dict):
            for key in ('sid', 'jti', 'refresh_jti'):
                if claims.get(key):
                    return str(claims[key])[:64]
    raw = getattr(request, 'META', {}).get('HTTP_AUTHORIZATION', '')
    if raw:
        # Last resort: the access token itself identifies this login.
        import hashlib
        return hashlib.sha256(raw.encode()).hexdigest()[:64]
    user = getattr(request, 'auth', None)
    return f'user:{getattr(user, "id", "anon")}'[:64]


def is_unlocked(request) -> bool:
    user = request.auth
    pin = get_pin(user.organization)
    if pin is None:
        return True          # no PIN means nothing is locked
    return ReportsPinUnlock.objects.filter(
        user=user,
        session_key=session_key(request),
        version=pin.version,
        expires_at__gt=timezone.now(),
    ).exists()


def _attempt_row(user) -> ReportsPinAttempt:
    row, _ = ReportsPinAttempt.objects.get_or_create(user=user)
    return row


def locked_until(user):
    row = ReportsPinAttempt.objects.filter(user=user).first()
    if row and row.locked_until and row.locked_until > timezone.now():
        return row.locked_until
    return None


def attempts_left(user) -> int:
    row = ReportsPinAttempt.objects.filter(user=user).first()
    used = row.failed_count if row else 0
    return max(0, max_attempts() - used)


def status(request) -> dict:
    user = request.auth
    pin = get_pin(user.organization)
    unlock = None
    if pin is not None:
        unlock = ReportsPinUnlock.objects.filter(
            user=user,
            session_key=session_key(request),
            version=pin.version,
            expires_at__gt=timezone.now(),
        ).first()
    blocked = locked_until(user)
    return {
        'configured': pin is not None,
        'unlocked': bool(unlock),
        'unlock_expires_at': unlock.expires_at.isoformat() if unlock else None,
        'can_manage': getattr(user, 'role', None) == 'admin',
        'locked_out_until': blocked.isoformat() if blocked else None,
        'attempts_left': attempts_left(user) if pin is not None else None,
    }


def _require_admin(user):
    if getattr(user, 'role', None) != 'admin':
        raise PinError('not_allowed', 'Only the main account can change the PIN.', 403)


def _require_password(user, password: str):
    """The admin's own password, every time. Never remembered."""
    if not password or not user.check_password(password):
        # Counted like a wrong PIN: without this the endpoint is a
        # password-guessing oracle for anyone holding a stolen token.
        _register_failure(user)
        raise PinError('password_incorrect', 'Incorrect account password.', 400)


def set_pin(request, pin: str, current_password: str) -> dict:
    user = request.auth
    _require_admin(user)
    _ensure_not_locked_out(user)
    _require_password(user, current_password)
    if not PIN_RE.match(pin or ''):
        raise PinError('pin_invalid', 'The PIN must be exactly 4 digits.', 400)

    row = get_pin(user.organization)
    if row is None:
        row = ReportsPin(organization=user.organization, version=1)
    else:
        row.version += 1       # kills every open unlock in the workspace
    row.pin_hash = make_password(pin)
    row.updated_by = user
    row.save()

    _clear_failures(user)
    # The admin who just set it should not have to type it immediately.
    _grant_unlock(request, row)
    logger.info('reports PIN set for %s by %s (v%s)', user.organization, user.email, row.version)
    return status(request)


def remove_pin(request, current_password: str) -> dict:
    user = request.auth
    _require_admin(user)
    _ensure_not_locked_out(user)
    _require_password(user, current_password)

    row = get_pin(user.organization)
    if row is not None:
        ReportsPinUnlock.objects.filter(user__organization=user.organization).delete()
        row.delete()
        logger.info('reports PIN removed for %s by %s', user.organization, user.email)
    _clear_failures(user)
    return status(request)


def verify(request, pin: str) -> dict:
    user = request.auth
    row = get_pin(user.organization)
    if row is None:
        raise PinError('pin_not_set', 'No PIN is set for this workspace.', 400)

    _ensure_not_locked_out(user)

    if not check_password(pin or '', row.pin_hash):
        left = _register_failure(user)
        if left <= 0:
            blocked = locked_until(user)
            raise PinError(
                'pin_locked_out', 'Too many wrong attempts.', 423,
                locked_until=blocked.isoformat() if blocked else None,
                retry_after_seconds=int((blocked - timezone.now()).total_seconds()) if blocked else 0,
            )
        raise PinError('pin_incorrect', 'Incorrect PIN.', 400, attempts_left=left)

    _clear_failures(user)
    _grant_unlock(request, row)
    return status(request)


def lock(request) -> dict:
    ReportsPinUnlock.objects.filter(
        user=request.auth, session_key=session_key(request)
    ).delete()
    return status(request)


def _grant_unlock(request, pin: ReportsPin):
    ReportsPinUnlock.objects.update_or_create(
        user=request.auth,
        session_key=session_key(request),
        defaults={
            'version': pin.version,
            'expires_at': timezone.now() + timedelta(minutes=unlock_minutes()),
        },
    )


def _ensure_not_locked_out(user):
    """A lock-out refuses the correct PIN too, or it is not a lock-out."""
    blocked = locked_until(user)
    if blocked:
        raise PinError(
            'pin_locked_out', 'Too many wrong attempts.', 423,
            locked_until=blocked.isoformat(),
            retry_after_seconds=int((blocked - timezone.now()).total_seconds()),
        )


def _register_failure(user) -> int:
    row = _attempt_row(user)
    row.failed_count += 1
    if row.failed_count >= max_attempts():
        row.locked_until = timezone.now() + timedelta(minutes=lockout_minutes())
        logger.warning('reports PIN lock-out for %s until %s', user.email, row.locked_until)
    row.save()
    return max(0, max_attempts() - row.failed_count)


def _clear_failures(user):
    ReportsPinAttempt.objects.filter(user=user).update(failed_count=0, locked_until=None)
