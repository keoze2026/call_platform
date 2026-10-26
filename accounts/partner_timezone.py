"""A buyer's or publisher's time zone (CH-260, Sir 25 Oct).

"If the portal time is set to EST, every buyer and publisher should be in EST
by default - it should only change if a buyer or publisher wants a different
time zone for themselves."

A partner's stored zone is blank until a zone of its own is chosen; blank
means "the portal's" (settings.PORTAL_TIMEZONE, Eastern). Choosing the
portal's own zone stores blank again, so the partner keeps following it.
"""
from zoneinfo import ZoneInfo

from django.conf import settings


def portal_timezone() -> str:
    return getattr(settings, 'PORTAL_TIMEZONE', '') or 'America/New_York'


def effective(partner) -> str:
    """The zone this partner's days and hours are counted in."""
    own = (getattr(partner, 'timezone', '') or '').strip()
    return own or portal_timezone()


def is_own(partner) -> bool:
    return bool((getattr(partner, 'timezone', '') or '').strip())


def to_store(name: str) -> str:
    """What to save for a picked zone: blank for the portal's own. Raises
    ValueError for a zone that does not exist."""
    name = (name or '').strip()
    if not name:
        return ''
    ZoneInfo(name)
    return '' if name == portal_timezone() else name
