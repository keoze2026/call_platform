import logging
import math
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from .models import DNIPool, DNINumber, DNISession
from accounts.models import User

logger = logging.getLogger(__name__)


def cooldown_minutes() -> int:
    """CH-456 (Sir, 31 Oct): after a visit ends, its number rests this long
    before another visitor is shown it, so a late call is not credited to the
    wrong visitor."""
    return int(getattr(settings, 'DNI_COOLDOWN_MINUTES', 10))


def _digits(number: str) -> str:
    return ''.join(c for c in (number or '') if c.isdigit())[-10:]


def _host_allowed(origin: str, domains) -> bool:
    """A pool with no domains listed works on any website; otherwise the page
    must be on one of them (or a sub-domain)."""
    wanted = [str(d or '').strip().lower().lstrip('.') for d in (domains or []) if str(d or '').strip()]
    if not wanted:
        return True
    host = (origin or '').lower()
    if '://' in host:
        host = host.split('://', 1)[1]
    host = host.split('/', 1)[0].split(':', 1)[0]
    if not host:
        return False
    for d in wanted:
        d = d.split('://', 1)[-1].split('/', 1)[0].split(':', 1)[0]
        if host == d or host.endswith('.' + d):
            return True
    return False


class DNIService:

    # ── Pool management ───────────────────────────────────────────────────────

    @staticmethod
    def create_pool(data, user: User) -> DNIPool:
        if not user.organization:
            raise ValueError("User has no organization")

        campaign = None
        if getattr(data, 'campaign_id', None):
            from campaigns.models import Campaign
            try:
                campaign = Campaign.objects.get(
                    id=data.campaign_id,
                    organization=user.organization
                )
            except Campaign.DoesNotExist:
                raise ValueError("Campaign not found")

        pool = DNIPool.objects.create(
            organization=user.organization,
            campaign=campaign,
            name=data.name,
            description=data.description or '',
            session_duration_minutes=data.session_duration_minutes or 30,
            fallback_number=data.fallback_number or '',
            allowed_domains=data.allowed_domains or [],
            replacement_number=getattr(data, 'replacement_number', '') or '',
            phone_number_format=getattr(data, 'phone_number_format', 'E164') or 'E164',
            vendor_enabled=getattr(data, 'vendor_enabled', False) or False,
            vendor_id=getattr(data, 'vendor_id', None),
            traffic_sources_enabled=getattr(data, 'traffic_sources_enabled', False) or False,
            traffic_sources=getattr(data, 'traffic_sources', []) or [],
        )
        idle = getattr(data, 'idle_time_sec', None)
        if idle:
            pool.session_duration_minutes = max(1, math.ceil(idle / 60))
            pool.save(update_fields=['session_duration_minutes'])
        ids = getattr(data, 'attached_number_ids', None)
        if ids:
            DNIService.sync_numbers(pool, ids, user)
        return pool

    @staticmethod
    def get_pool(pool_id: str, user: User) -> DNIPool:
        try:
            return DNIPool.objects.prefetch_related('numbers').get(
                id=pool_id,
                organization=user.organization
            )
        except DNIPool.DoesNotExist:
            raise ValueError("Pool not found")

    @staticmethod
    def list_pools(user: User):
        return DNIPool.objects.filter(
            organization=user.organization
        ).select_related('campaign').prefetch_related('numbers').order_by('-created_at')

    @staticmethod
    def update_pool(pool_id: str, data, user: User) -> DNIPool:
        pool = DNIService.get_pool(pool_id, user)

        if data.name is not None:
            pool.name = data.name
        if data.description is not None:
            pool.description = data.description
        if data.session_duration_minutes is not None:
            pool.session_duration_minutes = data.session_duration_minutes
        if data.fallback_number is not None:
            pool.fallback_number = data.fallback_number
        if data.allowed_domains is not None:
            pool.allowed_domains = data.allowed_domains
        for field in ['replacement_number', 'phone_number_format', 'vendor_enabled', 'vendor_id', 'traffic_sources_enabled', 'traffic_sources']:
            val = getattr(data, field, None)
            if val is not None:
                setattr(pool, field, val)
        if data.status is not None:
            pool.status = data.status
        # CH-456: the portal's switch, idle time and campaign.
        if getattr(data, 'active', None) is not None:
            pool.status = DNIPool.Status.ACTIVE if data.active else DNIPool.Status.PAUSED
        if getattr(data, 'idle_time_sec', None):
            pool.session_duration_minutes = max(1, math.ceil(data.idle_time_sec / 60))
        if getattr(data, 'campaign_id', None) is not None:
            if data.campaign_id:
                from campaigns.models import Campaign
                try:
                    pool.campaign = Campaign.objects.get(id=data.campaign_id, organization=user.organization)
                except Campaign.DoesNotExist:
                    raise ValueError("Campaign not found")
            else:
                pool.campaign = None

        pool.save()
        if getattr(data, 'attached_number_ids', None) is not None:
            DNIService.sync_numbers(pool, data.attached_number_ids, user)
        return pool

    @staticmethod
    def delete_pool(pool_id: str, user: User):
        pool = DNIService.get_pool(pool_id, user)
        pool.delete()

    @staticmethod
    def _phone(user: User, phone_id: str):
        from phone_numbers.models import PhoneNumber
        try:
            return PhoneNumber.objects.get(id=phone_id, organization=user.organization)
        except (PhoneNumber.DoesNotExist, ValueError, Exception):
            raise ValueError("Phone number not found")

    @staticmethod
    def add_number(pool_id: str, data, user: User) -> DNINumber:
        """CH-456: the portal sends the TFN's id from Phone Numbers
        (`number_id` / `phone_number_id`); a typed `number` still works."""
        pool = DNIService.get_pool(pool_id, user)
        phone_id = getattr(data, 'number_id', None) or getattr(data, 'phone_number_id', None)
        phone = DNIService._phone(user, phone_id) if phone_id else None
        number = phone.number if phone else (getattr(data, 'number', '') or '').strip()
        if not number:
            raise ValueError("Pick a number")
        return DNIService._attach(pool, number, phone)

    @staticmethod
    def _attach(pool: DNIPool, number: str, phone=None) -> DNINumber:
        digits = _digits(number)
        for n in DNINumber.objects.filter(pool__organization_id=pool.organization_id).select_related('pool'):
            if _digits(n.number) == digits:
                if n.pool_id == pool.id:
                    return n
                raise ValueError(f"{number} is already in the pool '{n.pool.name}'")
        return DNINumber.objects.create(pool=pool, number=number, phone_number=phone,
                                        status=DNINumber.Status.AVAILABLE)

    @staticmethod
    def _detach(n: DNINumber):
        """Take a number out of its pool; a visitor holding it keeps their
        visit on record but the number is no longer handed out."""
        DNISession.objects.filter(dni_number=n, status=DNISession.Status.ACTIVE).update(
            status=DNISession.Status.RELEASED, updated_at=timezone.now())
        n.delete()

    @staticmethod
    def remove_number(pool_id: str, number_id: str, user: User):
        """By the pool entry's id or by the TFN's id from Phone Numbers."""
        pool = DNIService.get_pool(pool_id, user)
        n = (DNINumber.objects.filter(pool=pool, id=number_id).first()
             if _is_uuid(number_id) else None)
        if n is None and _is_uuid(number_id):
            n = DNINumber.objects.filter(pool=pool, phone_number_id=number_id).first()
        if n is None:
            raise ValueError("Number not found in pool")
        DNIService._detach(n)

    @staticmethod
    def sync_numbers(pool: DNIPool, phone_ids, user: User):
        """CH-456: the pool page saves the whole list of attached TFNs; add
        the new ones and take out the ones no longer ticked."""
        wanted = {str(i) for i in (phone_ids or []) if i}
        current = {str(n.phone_number_id): n for n in pool.numbers.all() if n.phone_number_id}
        for pid, n in current.items():
            if pid not in wanted:
                DNIService._detach(n)
        for pid in wanted - set(current):
            phone = DNIService._phone(user, pid)
            DNIService._attach(pool, phone.number, phone)

    # ── Number assignment (called by JS snippet) ──────────────────────────────

    @staticmethod
    @transaction.atomic
    def assign_number(data, ip_address: str = '', user_agent: str = '', origin: str = '') -> dict:
        """
        Core DNI logic — assigns a free number from the pool to this visitor.
        If visitor already has an active session, returns same number.
        If pool is exhausted, returns fallback number.
        """
        # First expire any stale sessions
        DNIService.expire_sessions()

        try:
            pool = DNIPool.objects.select_for_update().get(
                id=data.pool_id,
                status=DNIPool.Status.ACTIVE
            )
        except DNIPool.DoesNotExist:
            raise ValueError("Pool not found or inactive")
        # CH-456: only the pool's own websites, when it lists any.
        if origin and not _host_allowed(origin, pool.allowed_domains):
            raise PermissionError("This website is not allowed for this pool")

        # Check if visitor already has an active session
        existing = DNISession.objects.filter(
            pool=pool,
            visitor_id=data.visitor_id,
            status=DNISession.Status.ACTIVE,
            expires_at__gt=timezone.now()
        ).first()

        if existing:
            # Extend session and return same number
            existing.expires_at = timezone.now() + timedelta(
                minutes=pool.session_duration_minutes
            )
            existing.save(update_fields=['expires_at', 'updated_at'])
            return {
                'phone_number': existing.assigned_number,
                'session_id':   str(existing.id),
                'expires_at':   existing.expires_at.isoformat(),
            }

        # Find an available number
        # CH-456: a number that has just finished a visit rests for the
        # cool-down, and the free number used longest ago goes first, so the
        # calls spread over the pool.
        available = (
            DNINumber.objects.select_for_update().filter(pool=pool, status=DNINumber.Status.AVAILABLE)
            .exclude(id__in=cooling_number_ids(pool))
            .annotate(last_used=Max('sessions__created_at'))
            .order_by(models_nulls_first('last_used'), 'created_at')
            .first()
        )

        if not available:
            # Pool exhausted — return fallback. Counted, so the pool page can
            # say the pool is too small (CH-456).
            _count_fallback(pool)
            fallback = pool.fallback_number or ''
            return {
                'phone_number': fallback,
                'session_id':   '',
                'expires_at':   '',
            }

        # Mark number as in use
        available.status = DNINumber.Status.IN_USE
        available.save(update_fields=['status'])

        # Create session
        expires_at = timezone.now() + timedelta(minutes=pool.session_duration_minutes)

        session = DNISession.objects.create(
            pool=pool,
            dni_number=available,
            visitor_id=data.visitor_id,
            ip_address=ip_address or None,
            user_agent=user_agent,
            utm_source=data.utm_source or '',
            utm_medium=data.utm_medium or '',
            utm_campaign=data.utm_campaign or '',
            utm_term=data.utm_term or '',
            utm_content=data.utm_content or '',
            gclid=data.gclid or '',
            fbclid=data.fbclid or '',
            referrer=data.referrer or '',
            landing_page=data.landing_page or '',
            assigned_number=available.number,
            status=DNISession.Status.ACTIVE,
            expires_at=expires_at,
        )

        return {
            'phone_number': available.number,
            'session_id':   str(session.id),
            'expires_at':   expires_at.isoformat(),
        }

    @staticmethod
    def release_number(assigned_number: str):
        """
        Called when a call comes in on a DNI number.
        Marks session as called and releases the number back to the pool.
        """
        session = DNISession.objects.filter(
            assigned_number=assigned_number,
            status=DNISession.Status.ACTIVE
        ).first()

        if session:
            session.status = DNISession.Status.CALLED
            session.save(update_fields=['status', 'updated_at'])

            if session.dni_number:
                session.dni_number.status = DNINumber.Status.AVAILABLE
                session.dni_number.save(update_fields=['status'])

        return session

    @staticmethod
    def get_session_by_number(assigned_number: str) -> Optional[DNISession]:
        """
        Used by the call webhook to attach UTM data to the call record.
        """
        return DNISession.objects.filter(
            assigned_number=assigned_number,
            status__in=[DNISession.Status.ACTIVE, DNISession.Status.CALLED]
        ).order_by('-created_at').first()

    @staticmethod
    def expire_sessions():
        """
        Marks expired sessions and releases their numbers back to pool.
        Called before every assignment.
        """
        now = timezone.now()
        expired = DNISession.objects.filter(
            status=DNISession.Status.ACTIVE,
            expires_at__lte=now
        ).select_related('dni_number')

        for session in expired:
            session.status = DNISession.Status.EXPIRED
            session.save(update_fields=['status', 'updated_at'])
            if session.dni_number:
                session.dni_number.status = DNINumber.Status.AVAILABLE
                session.dni_number.save(update_fields=['status'])

    @staticmethod
    def list_sessions(pool_id: str, user: User):
        try:
            pool = DNIPool.objects.get(id=pool_id, organization=user.organization)
        except DNIPool.DoesNotExist:
            raise ValueError("Pool not found")
        return DNISession.objects.filter(pool=pool).order_by('-created_at')[:100]

    @staticmethod
    def format_pool(pool: DNIPool) -> dict:
        numbers = list(pool.numbers.all())
        total = len(numbers)
        cooling_ids = cooling_number_ids(pool)
        in_use = sum(1 for n in numbers if n.status == DNINumber.Status.IN_USE)
        cooling = sum(1 for n in numbers if n.status == DNINumber.Status.AVAILABLE and n.id in cooling_ids)
        free = sum(1 for n in numbers if n.status == DNINumber.Status.AVAILABLE and n.id not in cooling_ids)
        peak = peak_visitors(pool)
        try:
            fallback = int(cache.get(_fallback_key(pool.id, timezone.localdate())) or 0)
        except Exception:
            fallback = 0
        return {
            'id':                       str(pool.id),
            'name':                     pool.name,
            'status':                   pool.status,
            'description':              pool.description,
            'campaign_id':              str(pool.campaign_id) if pool.campaign_id else None,
            'campaign_name':            pool.campaign.name if pool.campaign else '',
            'session_duration_minutes': pool.session_duration_minutes,
            'fallback_number':          pool.fallback_number,
            'allowed_domains':          pool.allowed_domains,
            'replacement_number':       pool.replacement_number,
            'phone_number_format':      pool.phone_number_format,
            'vendor_enabled':           pool.vendor_enabled,
            'vendor_id':                pool.vendor_id,
            'traffic_sources_enabled':  pool.traffic_sources_enabled,
            'traffic_sources':          pool.traffic_sources,
            'numbers': [
                {'id': str(n.id), 'number': n.number, 'status': n.status,
                 'phone_number_id': str(n.phone_number_id) if n.phone_number_id else None}
                for n in numbers
            ],
            'total_numbers':     total,
            'available_numbers': free,
            'organization_id':   str(pool.organization_id),
            'created_at':        pool.created_at.isoformat(),
            'updated_at':        pool.updated_at.isoformat(),
            # CH-456: what the portal reads, and the pool's figures.
            'active':              pool.status == DNIPool.Status.ACTIVE,
            'number_count':        total,
            'attached_number_ids': [str(n.phone_number_id) for n in numbers if n.phone_number_id],
            'idle_time_sec':       pool.session_duration_minutes * 60,
            'calls_today':         calls_today(pool, numbers),
            'numbers_in_use':      in_use,
            'numbers_cooling':     cooling,
            'numbers_free':        free,
            'peak_visitors_7d':    peak,
            'fallback_today':      fallback,
            'cooldown_minutes':    cooldown_minutes(),
            'too_small':           total == 0 or peak >= total or fallback > 0,
        }


def _is_uuid(value) -> bool:
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def models_nulls_first(field: str):
    from django.db.models import F
    return F(field).asc(nulls_first=True)


def cooling_number_ids(pool: DNIPool, now=None):
    """Numbers whose last visit ended inside the cool-down (CH-456)."""
    now = now or timezone.now()
    cut = now - timedelta(minutes=cooldown_minutes())
    return set(
        DNISession.objects.filter(pool=pool, dni_number__isnull=False).filter(
            Q(status__in=[DNISession.Status.CALLED, DNISession.Status.RELEASED], updated_at__gte=cut)
            | Q(status=DNISession.Status.EXPIRED, expires_at__gte=cut)
        ).values_list('dni_number_id', flat=True)
    )


def _fallback_key(pool_id, day) -> str:
    return f'dni:fallback:{pool_id}:{day.isoformat()}'


def _count_fallback(pool: DNIPool):
    try:
        key = _fallback_key(pool.id, timezone.localdate())
        if not cache.add(key, 1, 2 * 86400):
            cache.incr(key)
    except Exception:
        logger.warning('could not count a fallback for pool %s', pool.id, exc_info=True)


def peak_visitors(pool: DNIPool, days: int = 7, now=None) -> int:
    """Most visitors holding a number at the same moment over the last days."""
    now = now or timezone.now()
    since = now - timedelta(days=days)
    events = []
    for created, expires, updated, status in DNISession.objects.filter(
        pool=pool, created_at__lt=now,
    ).filter(Q(expires_at__gte=since) | Q(updated_at__gte=since)).values_list(
        'created_at', 'expires_at', 'updated_at', 'status',
    ):
        if status in (DNISession.Status.CALLED, DNISession.Status.RELEASED):
            end = updated
        else:
            end = min(expires, now)
        start = max(created, since)
        if end <= start:
            continue
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda e: (e[0], e[1]))
    peak = cur = 0
    for _, step in events:
        cur += step
        peak = max(peak, cur)
    return peak


def calls_today(pool: DNIPool, numbers) -> int:
    from routing.models import CallLog
    from zoneinfo import ZoneInfo
    from datetime import datetime, time
    digits = {_digits(n.number) for n in numbers if _digits(n.number)}
    if not digits:
        return 0
    variants = set()
    for d in digits:
        variants |= {d, '1' + d, '+1' + d, '+' + d}
    tz = ZoneInfo(getattr(settings, 'PORTAL_TIMEZONE', 'America/New_York'))
    start = datetime.combine(timezone.now().astimezone(tz).date(), time.min, tzinfo=tz)
    return CallLog.objects.filter(
        organization_id=pool.organization_id, created_at__gte=start,
        called_number__in=variants, retry_hidden=False,
    ).count()
