"""Handing pool numbers to website visitors (CH-460, Sir's number pool design).

  number    a visitor opens the page: they keep their number if they already
            have one, else get the free number used longest ago (not one
            resting after a visit - the pool's cool-down), else the fallback
  ping      every minute while the page is open: the visit is extended by the
            pool's idle limit
  release   the page is closed: the number stays held for the pool's closed-
            browser delay, then the visit ends and the cool-down starts

Each request is counted as an impression; a visitor shown the fallback is a
miss (an "overflow" visit, kept so the pool page can say it is too small).
"""
import logging
import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from dni.models import DNILander, DNINumber, DNIPool, DNISession

logger = logging.getLogger(__name__)

VID = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
SNIPPET_SEEN_EVERY = timedelta(seconds=60)


def digits10(number: str) -> str:
    d = ''.join(c for c in (number or '') if c.isdigit())
    return d[1:] if len(d) == 11 and d.startswith('1') else d[-10:]


def display(number: str) -> str:
    d = digits10(number)
    return f'1-{d[:3]}-{d[3:6]}-{d[6:]}' if len(d) == 10 else (number or '')


def idle_seconds(pool: DNIPool) -> int:
    return max(60, int(pool.session_duration_minutes or 5) * 60)


def cooling_ids(pool: DNIPool, now=None) -> set:
    """Numbers whose last visit ended inside the pool's cool-down."""
    now = now or timezone.now()
    cut = now - timedelta(seconds=int(pool.cooldown_seconds or 0))
    return set(
        DNISession.objects.filter(pool=pool, dni_number__isnull=False).filter(
            Q(status__in=[DNISession.Status.CALLED, DNISession.Status.RELEASED], updated_at__gte=cut)
            | Q(status=DNISession.Status.EXPIRED, expires_at__gte=cut)
        ).values_list('dni_number_id', flat=True)
    )


def _day():
    return timezone.localdate().isoformat()


def _incr(key: str, ttl: int) -> int:
    try:
        if cache.add(key, 1, ttl):
            return 1
        return cache.incr(key)
    except Exception:
        return 0


def impressions_today(pool: DNIPool) -> int:
    try:
        return int(cache.get(f'dni:imp:{pool.id}:{_day()}') or 0)
    except Exception:
        return 0


def _ip_allowed(pool: DNIPool, ip: str, limit: int) -> bool:
    if not ip or not limit:
        return True
    return _incr(f'dni:ip:{pool.id}:{ip}', 600) <= limit


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or '').lower()
    except Exception:
        return ''


def _page(url: str) -> str:
    try:
        u = urlsplit(url)
        return f'{(u.hostname or "").lower()}{u.path.rstrip("/")}'
    except Exception:
        return ''


def _note_snippet(pool: DNIPool, page_url: str, origin: str):
    """"Snippet seen on bing-landing.com · 2 min ago" - at most once a minute."""
    now = timezone.now()
    host = _host(page_url) or _host(origin)
    if pool.snippet_seen_at is None or now - pool.snippet_seen_at > SNIPPET_SEEN_EVERY or pool.snippet_seen_host != host:
        DNIPool.objects.filter(pk=pool.pk).update(snippet_seen_at=now, snippet_seen_host=host[:255])
        page = _page(page_url)
        if page:
            for lander in DNILander.objects.filter(pool=pool):
                if _page(lander.url) == page:
                    DNILander.objects.filter(pk=lander.pk).update(snippet_seen_at=now)


def captured_from(pool: DNIPool, query: str) -> dict:
    """The pool's chosen URL parameters from the landing page's query."""
    wanted = [str(k).strip() for k in (pool.capture_params or []) if str(k).strip()]
    got = {}
    for k, v in parse_qsl((query or '').lstrip('?'), keep_blank_values=False)[:50]:
        if k in wanted and k not in got:
            got[k] = v[:255]
    return got


def swap_list(pool: DNIPool) -> list:
    """The numbers the script replaces on the page: those ticked on each
    lander, plus the fallback (the number written in the page)."""
    out = []
    for lander in DNILander.objects.filter(pool=pool):
        out += [digits10(n) for n in (lander.swap or [])]
    fb = digits10(pool.fallback_number)
    if fb:
        out.append(fb)
    return sorted({d for d in out if len(d) == 10})


def _answer(pool, number=None, session=None):
    fallback = number is None
    shown = pool.fallback_number if fallback else number.number
    return {
        'number': digits10(shown),
        'display': display(shown),
        'fallback': fallback,
        'swap': swap_list(pool),
        'session_id': str(session.id) if session else '',
        'expires_at': session.expires_at.isoformat() if session and not fallback else '',
    }


@transaction.atomic
def give_number(pool: DNIPool, visitor_id: str, *, fields: dict, ip: str = '', user_agent: str = '',
                origin: str = '', page_url: str = '', rules: dict = None) -> dict:
    """The number this visitor sees (see the module text)."""
    from dni.protection import rules as pool_rules
    rules = rules or pool_rules(pool)
    now = timezone.now()
    _incr(f'dni:imp:{pool.id}:{_day()}', 2 * 86400)
    try:
        _note_snippet(pool, page_url, origin)
    except Exception:
        logger.warning('snippet-seen not saved for pool %s', pool.id, exc_info=True)

    held = (DNISession.objects.select_for_update()
            .filter(pool=pool, visitor_id=visitor_id, status=DNISession.Status.ACTIVE,
                    expires_at__gt=now, dni_number__isnull=False)
            .exclude(dni_number__status=DNINumber.Status.QUARANTINED)
            .select_related('dni_number').order_by('-created_at').first())
    if held:
        held.expires_at = now + timedelta(seconds=idle_seconds(pool))
        held.last_seen_at = now
        held.save(update_fields=['expires_at', 'last_seen_at', 'updated_at'])
        return _answer(pool, held.dni_number, held)

    if not _ip_allowed(pool, ip, rules.get('assign_per_ip_10m', 30)):
        # A bot asking for number after number: shown the fallback, no visit kept.
        return _answer(pool)

    number = (DNINumber.objects.select_for_update(skip_locked=True)
              .filter(pool=pool, status=DNINumber.Status.AVAILABLE)
              .exclude(id__in=cooling_ids(pool, now))
              .order_by(F('last_assigned_at').asc(nulls_first=True), 'created_at')
              .first())
    if number:
        number.status = DNINumber.Status.IN_USE
        number.last_assigned_at = now
        number.save(update_fields=['status', 'last_assigned_at'])

    session = DNISession.objects.create(
        pool=pool, dni_number=number, visitor_id=visitor_id, ip_address=ip or None,
        user_agent=(user_agent or '')[:500],
        assigned_number=number.number if number else '',
        overflow=number is None,
        status=DNISession.Status.ACTIVE if number else DNISession.Status.EXPIRED,
        expires_at=now + timedelta(seconds=idle_seconds(pool)) if number else now,
        last_seen_at=now,
        **fields,
    )
    return _answer(pool, number, session if number else None)


def keep(pool: DNIPool, visitor_id: str) -> dict:
    """The page is still open: extend the visit by the idle limit."""
    now = timezone.now()
    s = (DNISession.objects.filter(pool=pool, visitor_id=visitor_id, status=DNISession.Status.ACTIVE,
                                   expires_at__gt=now, dni_number__isnull=False)
         .exclude(dni_number__status=DNINumber.Status.QUARANTINED)
         .select_related('dni_number').order_by('-created_at').first())
    if s is None:
        return {'ok': False}
    DNISession.objects.filter(pk=s.pk).update(
        expires_at=now + timedelta(seconds=idle_seconds(pool)), last_seen_at=now, updated_at=now)
    return {'ok': True, 'number': digits10(s.dni_number.number), 'display': display(s.dni_number.number)}


def let_go(pool: DNIPool, visitor_id: str) -> dict:
    """The page was closed: hold the number for the closed-browser delay."""
    now = timezone.now()
    until = now + timedelta(seconds=int(pool.closed_browser_delay_seconds or 0))
    n = DNISession.objects.filter(pool=pool, visitor_id=visitor_id, status=DNISession.Status.ACTIVE,
                                  expires_at__gt=until).update(expires_at=until, updated_at=now)
    return {'ok': bool(n)}


def visit_fields(pool: DNIPool, params: dict) -> dict:
    """The visit's stored fields from the script's request."""
    q = params.get('q', '') or ''
    from_url = dict(parse_qsl(q.lstrip('?'))) if q else {}

    def pick(name, n=255):
        v = params.get(name) or from_url.get(name) or ''
        return str(v)[:n]

    return {
        'utm_source': pick('utm_source'), 'utm_medium': pick('utm_medium'),
        'utm_campaign': pick('utm_campaign'), 'utm_term': pick('utm_term'),
        'utm_content': pick('utm_content'), 'gclid': pick('gclid'), 'fbclid': pick('fbclid'),
        'msclkid': pick('msclkid'), 'sub_id': pick('sub_id'),
        'referrer': pick('ref', 200), 'landing_page': pick('url', 200),
        'captured': captured_from(pool, q),
    }
