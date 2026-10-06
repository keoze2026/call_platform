"""Refuse historical report data to a session that has not entered the PIN.

Without this the PIN is decoration again: it would be stored, hashed and
verified on the server, and the data would still be readable by anyone with a
token. The lock is this file.

Call `require_reports_unlock` at the top of every endpoint that can return
anything dated before today.
"""
import logging

from django.utils import timezone

from .services import get_pin, is_unlocked

logger = logging.getLogger(__name__)


class ReportsPinRequired(Exception):
    """Raised to produce 423 Locked with code reports_pin_required."""

    code = 'reports_pin_required'
    detail = 'Enter the reports PIN to view reports before today.'


def _resolve_tz(filters):
    """The zone the report itself buckets in.

    Reuses analytics._tz so "today" for the PIN is exactly the "today" the
    numbers use. If they disagreed, a report could be refused while showing
    data, or shown while being refused.
    """
    try:
        from analytics.services import _tz
        return _tz(filters)
    except Exception:
        return timezone.get_current_timezone()


def _is_api_key(request) -> bool:
    """Was this request authenticated by an API key rather than a login?

    APIKeyAuth sets `auth_org` on the request and nothing else does; key tokens
    are also prefixed `avx_`. Either is enough on its own.
    """
    if getattr(request, 'auth_org', None) is not None:
        return True
    header = getattr(request, 'META', {}).get('HTTP_AUTHORIZATION', '')
    return 'avx_' in header


def _first(filters, *names):
    for name in names:
        value = getattr(filters, name, None) if filters is not None else None
        if value is None and isinstance(filters, dict):
            value = filters.get(name)
        if value:
            return value
    return None


def is_historical(filters, tzinfo=None) -> bool:
    """Could this request return anything from before today?

    No start date means unbounded, which includes history - so it counts as
    historical. Erring the other way would hand over everything to any request
    that simply omits the dates.
    """
    from django.utils.dateparse import parse_date, parse_datetime

    tzinfo = tzinfo or _resolve_tz(filters)
    today = timezone.now().astimezone(tzinfo).date()

    raw = _first(filters, 'date_from', 'start_date', 'created_at__gte')
    if not raw:
        return True

    start = parse_date(str(raw)[:10])
    if start is None:
        dt = parse_datetime(str(raw))
        start = dt.date() if dt else None
    if start is None:
        return True          # unparseable: treat as unbounded rather than open

    return start < today


def require_reports_unlock(request, filters=None, *, started_at=None):
    """Raise ReportsPinRequired unless this request may see history.

    Does nothing when the organisation has no PIN, so installing this changes
    no behaviour until an admin sets one.

    `started_at` is for single-record endpoints - a call detail or a recording -
    where the date comes from the record rather than a filter.
    """
    user = getattr(request, 'auth', None)
    organization = getattr(user, 'organization', None)
    if organization is None:
        return

    # API keys are exempt. A key cannot type a PIN, so refusing it would break
    # an integration the admin deliberately created, silently and with no way
    # for anyone to fix it from the interface. The PIN exists to stop a person
    # browsing history in the portal; a key is not a person.
    if _is_api_key(request):
        return

    if get_pin(organization) is None:
        return

    if started_at is not None:
        tzinfo = _resolve_tz(filters)
        today = timezone.now().astimezone(tzinfo).date()
        if started_at.astimezone(tzinfo).date() >= today:
            return
    elif not is_historical(filters):
        return

    if is_unlocked(request):
        return

    raise ReportsPinRequired()
