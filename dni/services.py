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
    """CH-456's server-wide cool-down; since CH-460 each pool has its own
    (DNIPool.cooldown_seconds). Kept for anything still asking."""
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
            session_duration_minutes=data.session_duration_minutes or 5,
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
        DNIService._apply_design_fields(pool, data)
        pool.save()
        ids = getattr(data, 'attached_number_ids', None)
        if ids:
            DNIService.sync_numbers(pool, ids, user)
        for lander in getattr(data, 'landers', None) or []:
            DNIService.add_lander(pool, lander.get('url', ''), swap=lander.get('swap'), found=lander.get('found'))
        return pool

    @staticmethod
    def _apply_design_fields(pool: DNIPool, data):
        """CH-460: country, cool-down, closed-browser delay, captured URL
        parameters and the spam rules (when sent)."""
        from dni.protection import clean_rules
        if getattr(data, 'country', None):
            pool.country = str(data.country).strip().upper()[:2]
        if getattr(data, 'cooldown_seconds', None) is not None:
            pool.cooldown_seconds = max(0, min(int(data.cooldown_seconds), 3600))
        if getattr(data, 'closed_browser_delay_seconds', None) is not None:
            pool.closed_browser_delay_seconds = max(0, min(int(data.closed_browser_delay_seconds), 3600))
        if getattr(data, 'closed_browser_delay_sec', None) is not None:
            pool.closed_browser_delay_seconds = max(0, min(int(data.closed_browser_delay_sec), 3600))
        if getattr(data, 'capture_params', None) is not None:
            pool.capture_params = [str(k).strip()[:40] for k in data.capture_params if str(k).strip()][:20]
        if getattr(data, 'protection', None) is not None:
            pool.protection = {**(pool.protection or {}), **clean_rules(data.protection)}

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

        DNIService._apply_design_fields(pool, data)
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
    def assign_number(data, ip_address: str = '', user_agent: str = '', origin: str = '') -> dict:
        """The first snippet's POST /api/dni/assign, kept working: the same
        logic as /api/dni/v1/number (dni/visitor.py)."""
        from dni.visitor import give_number
        DNIService.expire_sessions()
        try:
            pool = DNIPool.objects.get(id=data.pool_id, status=DNIPool.Status.ACTIVE)
        except (DNIPool.DoesNotExist, ValueError, Exception):
            raise ValueError("Pool not found or inactive")
        if origin and not _host_allowed(origin, pool.allowed_domains):
            raise PermissionError("This website is not allowed for this pool")
        fields = {k: (getattr(data, k, '') or '')[:255] for k in
                  ('utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'gclid', 'fbclid')}
        fields['referrer'] = (getattr(data, 'referrer', '') or '')[:200]
        fields['landing_page'] = (getattr(data, 'landing_page', '') or '')[:200]
        out = give_number(pool, data.visitor_id, fields=fields, ip=ip_address, user_agent=user_agent,
                          origin=origin, page_url=fields['landing_page'])
        shown = pool.fallback_number if out['fallback'] else ('+1' + out['number'] if len(out['number']) == 10 else out['number'])
        if not out['fallback']:
            from dni.models import DNISession as _S
            s = _S.objects.filter(id=out['session_id']).first()
            shown = s.assigned_number if s else shown
        return {'phone_number': shown, 'session_id': out['session_id'], 'expires_at': out['expires_at']}

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
    def format_pool(pool: DNIPool, detail: bool = True) -> dict:
        """Everything the pool list and the pool page show (CH-460)."""
        from dni.models import DNICall
        from dni.protection import blocked_counts, rules
        from dni.visitor import display, impressions_today, swap_list

        now = timezone.now()
        day_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
        numbers = list(pool.numbers.all().order_by('number'))
        total = len(numbers)
        cooling = cooling_number_ids(pool, now)

        def state(n):
            if n.status == DNINumber.Status.QUARANTINED:
                return 'quarantined'
            if n.status == DNINumber.Status.IN_USE:
                return 'in_use'
            return 'cooling' if n.id in cooling else 'available'

        states = {n.id: state(n) for n in numbers}
        in_use = sum(1 for v in states.values() if v == 'in_use')
        cool = sum(1 for v in states.values() if v == 'cooling')
        free = sum(1 for v in states.values() if v == 'available')
        quarantined = sum(1 for v in states.values() if v == 'quarantined')
        peak = peak_visitors(pool)
        misses = DNISession.objects.filter(pool=pool, overflow=True, created_at__gte=day_start).count()
        calls = DNICall.objects.filter(pool=pool, started_at__gte=day_start, blocked_reason='')
        calls_n = calls.count()
        attributed = calls.filter(session__isnull=False).count()
        try:
            fallback = int(cache.get(_fallback_key(pool.id, timezone.localdate())) or 0)
        except Exception:
            fallback = 0
        if pool.status != DNIPool.Status.ACTIVE:
            label = 'paused'
        elif total == 0 or misses > 0 or quarantined > 0 or (total and in_use >= total):
            label = 'attention'
        else:
            label = 'active'

        out = {
            'id':                       str(pool.id),
            'name':                     pool.name,
            'status':                   pool.status,
            'status_label':             label,
            'public_key':               pool.public_key or '',
            'description':              pool.description,
            'campaign_id':              str(pool.campaign_id) if pool.campaign_id else None,
            'campaign_name':            pool.campaign.name if pool.campaign else '',
            'country':                  pool.country,
            'session_duration_minutes': pool.session_duration_minutes,
            'cooldown_seconds':         pool.cooldown_seconds,
            'closed_browser_delay_seconds': pool.closed_browser_delay_seconds,
            'capture_params':           pool.capture_params or [],
            'protection':               rules(pool),
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
            # CH-456 names the portal reads.
            'active':              pool.status == DNIPool.Status.ACTIVE,
            'number_count':        total,
            'attached_number_ids': [str(n.phone_number_id) for n in numbers if n.phone_number_id],
            'idle_time_sec':       pool.session_duration_minutes * 60,
            'closed_browser_delay_sec': pool.closed_browser_delay_seconds,
            # Every call on the pool's numbers today (call logs, as in CH-456) -
            # also counts calls from before the pool kept its own call list.
            'calls_today':         max(calls_n, calls_today(pool, numbers)),
            'numbers_in_use':      in_use,
            'numbers_cooling':     cool,
            'numbers_free':        free,
            'numbers_quarantined': quarantined,
            'peak_visitors_7d':    peak,
            'fallback_today':      max(fallback, misses),
            'cooldown_minutes':    max(1, round(pool.cooldown_seconds / 60)) if pool.cooldown_seconds else 0,
            'too_small':           total == 0 or peak >= total or misses > 0,
            # CH-460: Sir's design figures.
            'impressions_today':   impressions_today(pool),
            'misses_today':        misses,
            'attributed_today':    attributed,
            'snippet_seen_at':     pool.snippet_seen_at.isoformat() if pool.snippet_seen_at else None,
            'snippet_seen_host':   pool.snippet_seen_host,
            'landers_count':       pool.landers.count(),
        }
        if not detail:
            return out

        live = {s.dni_number_id: s for s in DNISession.objects.filter(
            pool=pool, status=DNISession.Status.ACTIVE, expires_at__gt=now, dni_number__isnull=False)}
        per_number = {}
        for nid in calls.values_list('number_id', flat=True):
            per_number[nid] = per_number.get(nid, 0) + 1

        def source(s):
            if s is None:
                return ''
            parts = [p for p in (s.utm_source, s.utm_term or s.utm_campaign) if p]
            return ' / '.join(parts) or (s.captured or {}).get('sub_id', '') or 'direct'

        out['number_rows'] = [{
            'id': str(n.id),
            'number': n.number,
            'display': display(n.number),
            'phone_number_id': str(n.phone_number_id) if n.phone_number_id else None,
            'state': states[n.id],
            'shown_to': (n.status_reason if states[n.id] == 'quarantined' else source(live.get(n.id))),
            'held_for_sec': int((now - live[n.id].created_at).total_seconds()) if n.id in live else None,
            'calls_today': per_number.get(n.id, 0),
        } for n in numbers]

        by_source = {}
        for c in calls.select_related('session'):
            key = source(c.session) if c.session_id else 'Not attributed'
            by_source[key] = by_source.get(key, 0) + 1
        out['calls_by_source'] = sorted(({'source': k, 'calls': v} for k, v in by_source.items()),
                                        key=lambda x: (x['source'] == 'Not attributed', -x['calls']))
        out['spam'] = blocked_counts(pool, day_start)
        out['blocked_today'] = DNICall.objects.filter(pool=pool, started_at__gte=day_start).exclude(blocked_reason='').count()
        out['landers'] = [DNIService.format_lander(l) for l in pool.landers.all()]
        out['swap_numbers'] = swap_list(pool)
        return out

    # ── CH-460: numbers in and out of rotation ────────────────────────────────

    @staticmethod
    def set_number_status(pool_id: str, number_id: str, status: str, reason: str, user: User) -> DNINumber:
        """Pull a number out of rotation (quarantined) or put it back."""
        pool = DNIService.get_pool(pool_id, user)
        n = (DNINumber.objects.filter(pool=pool, id=number_id).first() if _is_uuid(number_id) else None) \
            or (DNINumber.objects.filter(pool=pool, phone_number_id=number_id).first() if _is_uuid(number_id) else None)
        if n is None:
            raise ValueError("Number not found in pool")
        now = timezone.now()
        if status == 'quarantined':
            DNISession.objects.filter(dni_number=n, status=DNISession.Status.ACTIVE).update(
                status=DNISession.Status.RELEASED, updated_at=now)
            n.status, n.status_reason = DNINumber.Status.QUARANTINED, (reason or 'Pulled by hand')[:255]
        elif status in ('available', 'active'):
            n.status, n.status_reason = DNINumber.Status.AVAILABLE, ''
        else:
            raise ValueError("Status must be quarantined or available")
        n.status_changed_at = now
        n.save(update_fields=['status', 'status_reason', 'status_changed_at'])
        return n

    # ── CH-460: landers ───────────────────────────────────────────────────────

    @staticmethod
    def format_lander(l) -> dict:
        return {
            'id': str(l.id), 'url': l.url, 'found': l.found or [], 'swap': l.swap or [],
            'scan_error': l.scan_error,
            'last_scanned_at': l.last_scanned_at.isoformat() if l.last_scanned_at else None,
            'snippet_seen_at': l.snippet_seen_at.isoformat() if l.snippet_seen_at else None,
        }

    @staticmethod
    def add_lander(pool: DNIPool, url: str, swap=None, found=None):
        from dni.models import DNILander
        from dni.scanner import ScanError, scan
        from dni.visitor import digits10
        url = (url or '').strip()
        if not url:
            raise ValueError("Enter the landing page address")
        if not url.lower().startswith(('http://', 'https://')):
            url = 'https://' + url
        lander = DNILander(pool=pool, url=url[:1000])
        if found is None:
            try:
                found = scan(url)
                lander.scan_error = ''
            except ScanError as e:
                found, lander.scan_error = [], str(e)[:255]
            except Exception:
                found, lander.scan_error = [], 'The page could not be read.'
            lander.last_scanned_at = timezone.now()
        else:
            found = [
                {'number': digits10(f.get('number', '')), 'display': str(f.get('display', ''))[:20],
                 'where': str(f.get('where', ''))[:200], 'label': str(f.get('label', ''))[:40]}
                for f in (found if isinstance(found, list) else [])[:50]
                if isinstance(f, dict) and len(digits10(str(f.get('number', '')))) == 10
            ]
        lander.found = found or []
        picked = swap if swap is not None else [f['number'] for f in lander.found]
        lander.swap = sorted({digits10(str(d)) for d in picked if len(digits10(str(d))) == 10})
        lander.save()
        return lander

    @staticmethod
    def rescan_lander(lander):
        from dni.scanner import ScanError, scan
        try:
            lander.found, lander.scan_error = scan(lander.url), ''
        except ScanError as e:
            lander.scan_error = str(e)[:255]
        except Exception:
            lander.scan_error = 'The page could not be read.'
        lander.last_scanned_at = timezone.now()
        if not lander.swap:
            lander.swap = sorted({f['number'] for f in lander.found})
        lander.save()
        return lander

    @staticmethod
    def get_lander(lander_id: str, user: User):
        from dni.models import DNILander
        if not _is_uuid(lander_id):
            raise ValueError("Lander not found")
        l = DNILander.objects.select_related('pool').filter(id=lander_id, pool__organization=user.organization).first()
        if l is None:
            raise ValueError("Lander not found")
        return l

    # ── CH-460: calls and complaints ──────────────────────────────────────────

    @staticmethod
    def list_calls(pool_id: str, user: User, limit: int = 100) -> list:
        from dni.models import DNICall
        from dni.visitor import display
        pool = DNIService.get_pool(pool_id, user)
        rows = (DNICall.objects.filter(pool=pool).select_related('number', 'session', 'call_log')
                .order_by('-started_at')[:max(1, min(limit, 500))])
        out = []
        for c in rows:
            s = c.session
            out.append({
                'id': c.id,
                'started_at': c.started_at.isoformat(),
                'caller': c.caller_id,
                'number': display(c.number.number) if c.number else display(c.called),
                'source': (' / '.join(p for p in (s.utm_source, s.utm_term or s.utm_campaign) if p) or 'direct') if s else '',
                'utm': ({k: getattr(s, k) for k in ('utm_source', 'utm_medium', 'utm_campaign', 'utm_term',
                                                     'utm_content', 'gclid', 'msclkid', 'fbclid') if getattr(s, k)}
                        | (s.captured or {})) if s else {},
                'landing_page': s.landing_page if s else '',
                'blocked_reason': c.blocked_reason,
                'duration_sec': c.duration_sec,
                'call_log_id': str(c.call_log_id) if c.call_log_id else None,
                'status': c.call_log.status if c.call_log else ('blocked' if c.blocked_reason else ''),
            })
        return out


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
    """Numbers whose last visit ended inside the pool's cool-down (CH-460:
    per pool, Sir's design default 2 minutes)."""
    from dni.visitor import cooling_ids
    return cooling_ids(pool, now)


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
