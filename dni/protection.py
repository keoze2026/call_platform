"""Number pool spam-attack rules (CH-460, Sir's design).

Runs at the two points our phone system already reports to us - the incoming
call (routing/asterisk_handler.route_incoming_call) and the hang-up
(call_ended) - so nothing changes on Asterisk.

  repeat caller   the same caller calling the pool's numbers more than
                  `max_calls_per_caller_10m` times in 10 minutes is refused and
                  blocked for `block_hours` (default 24)
  number flood    one number getting more than `number_flood_per_min` calls in
                  a minute is pulled out of rotation (quarantined) and the call
                  refused - the attack stays on that number
  silent calls    `short_calls_per_caller_30m` calls of under `short_call_sec`
                  seconds from one caller in 30 minutes (robot / no-voice
                  calls) block the caller for `block_hours`
  anonymous       calls with no caller ID refused, when switched on

Blocks go to Blocked Numbers (spam_protection.Blacklist, automatic, with an
expiry), on the pool's campaign. Every check is fail-open: if anything here
goes wrong, the call carries on as if there were no pool.
"""
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from django.utils import timezone

logger = logging.getLogger(__name__)

RULES = {
    'max_calls_per_caller_10m': 5,
    'number_flood_per_min': 20,
    'short_call_sec': 4,
    'short_calls_per_caller_30m': 3,
    'block_anonymous': False,
    'assign_per_ip_10m': 30,
    'call_grace_sec': 600,
    'block_hours': 24,
}

NOTE_REPEAT = 'Number pool: too many calls'
NOTE_SILENT = 'Number pool: repeated silent/short calls'
FLOOD_REASON = 'Call flood'


def rules(pool) -> dict:
    """The pool's rules, with the defaults for anything not set."""
    out = dict(RULES)
    for k, v in (getattr(pool, 'protection', None) or {}).items():
        if k in RULES:
            out[k] = bool(v) if isinstance(RULES[k], bool) else int(v)
    return out


def _rule_key(k) -> str:
    """The rule's name however it was spelt: the portal's key converter
    turns max_calls_per_caller_10m into max_calls_per_caller10m."""
    flat = str(k).replace('_', '').lower()
    for name in RULES:
        if name.replace('_', '') == flat:
            return name
    return ''


def clean_rules(raw) -> dict:
    """Only known keys, with sane bounds (used when a pool is saved)."""
    out = {}
    for k, v in (raw or {}).items():
        k = _rule_key(k)
        if not k or v is None:
            continue
        if isinstance(RULES[k], bool):
            out[k] = bool(v)
        else:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            out[k] = max(0, min(n, 100000))
    return out


def _digits(number: str) -> str:
    d = ''.join(c for c in (number or '') if c.isdigit())
    return d[1:] if len(d) == 11 and d.startswith('1') else d[-10:]


def pool_number_for(called: str):
    """The pool number a call came in on, or None (not a pool number)."""
    from dni.models import DNINumber, DNIPool
    from spam_protection.numbers import variants
    return (DNINumber.objects.select_related('pool')
            .filter(number__in=variants(called), pool__status=DNIPool.Status.ACTIVE)
            .first())


@dataclass
class Inbound:
    number: object = None
    pool: object = None
    caller: str = ''
    blocked: str = ''
    rules: dict = field(default_factory=dict)


def _block_caller(pool, caller: str, note: str, hours: int):
    from spam_protection.models import Blacklist
    if not caller:
        return
    until = timezone.now() + timedelta(hours=max(1, hours))
    entry = Blacklist.objects.filter(organization_id=pool.organization_id, campaign_id=pool.campaign_id,
                                     phone_number=caller, buyer__isnull=True, publisher__isnull=True).first()
    if entry:
        entry.is_active, entry.expires_at, entry.notes = True, until, f'{note} ({pool.name})'
        entry.reason = Blacklist.Reason.AUTO
        entry.save(update_fields=['is_active', 'expires_at', 'notes', 'reason'])
    else:
        Blacklist.objects.create(organization_id=pool.organization_id, campaign_id=pool.campaign_id,
                                 phone_number=caller, reason=Blacklist.Reason.AUTO, is_active=True,
                                 expires_at=until, notes=f'{note} ({pool.name})')
    logger.warning('number pool %s: blocked %s for %sh - %s', pool.id, caller, hours, note)


def check_inbound(called: str, caller: str) -> Optional[Inbound]:
    """Before the call is routed. Returns None for a call that is not on a
    pool number; otherwise the context, with `blocked` set when the pool's
    rules refuse the call (the refused call is recorded here)."""
    from dni.models import DNICall, DNINumber
    try:
        n = pool_number_for(called)
        if n is None:
            return None
        ctx = Inbound(number=n, pool=n.pool, caller=caller or '', rules=rules(n.pool))
        r, now = ctx.rules, timezone.now()
        if n.status == DNINumber.Status.QUARANTINED:
            # Pulled out of rotation: the attack stays on this number.
            ctx.blocked = 'quarantined'
        elif not _digits(caller):
            if r['block_anonymous']:
                ctx.blocked = 'anonymous'
        else:
            recent = DNICall.objects.filter(pool__organization_id=n.pool.organization_id, caller_id=caller,
                                            started_at__gte=now - timedelta(minutes=10)).count()
            if r['max_calls_per_caller_10m'] and recent >= r['max_calls_per_caller_10m']:
                ctx.blocked = 'caller_rate'
                _block_caller(n.pool, caller, NOTE_REPEAT, r['block_hours'])
        if not ctx.blocked and r['number_flood_per_min']:
            since = now - timedelta(minutes=1)
            if n.status_changed_at and n.status_changed_at > since:
                since = n.status_changed_at  # restored by hand: count from then
            flood = DNICall.objects.filter(number=n, blocked_reason='', started_at__gte=since).count()
            if flood >= r['number_flood_per_min']:
                ctx.blocked = 'number_flood'
                DNINumber.objects.filter(pk=n.pk).update(
                    status=DNINumber.Status.QUARANTINED, status_changed_at=now,
                    status_reason=f'{FLOOD_REASON} · {flood + 1}/min')
        if ctx.blocked:
            DNICall.objects.create(pool=n.pool, number=n, caller_id=caller or '', called=called,
                                   blocked_reason=ctx.blocked)
        return ctx
    except Exception:
        logger.exception('number pool inbound check failed for %s -> %s; call continues', caller, called)
        return None


def record_call(ctx: Optional[Inbound], call_log):
    """After the call row exists: keep the pool call, tied to the visit."""
    if ctx is None or ctx.blocked:
        return
    from dni.attribution import visit_for_call
    from dni.models import DNICall
    try:
        visit = visit_for_call(call_log)
        DNICall.objects.create(pool=ctx.pool, number=ctx.number, session=visit, call_log=call_log,
                               caller_id=ctx.caller, called=call_log.called_number)
    except Exception:
        logger.exception('number pool call not recorded for %s', getattr(call_log, 'id', ''))


def on_call_end(call_log):
    """At hang-up: the call's length, and the silent-caller rule."""
    from dni.models import DNICall
    try:
        pc = DNICall.objects.select_related('pool').filter(call_log=call_log).first()
        if pc is None:
            return
        seconds = max(0, int((timezone.now() - pc.started_at).total_seconds()))
        DNICall.objects.filter(pk=pc.pk).update(duration_sec=seconds)
        r = rules(pc.pool)
        if not pc.caller_id or not r['short_call_sec'] or seconds >= r['short_call_sec']:
            return
        short = DNICall.objects.filter(
            pool__organization_id=pc.pool.organization_id, caller_id=pc.caller_id, blocked_reason='',
            duration_sec__lt=r['short_call_sec'], started_at__gte=timezone.now() - timedelta(minutes=30),
        ).count()
        if r['short_calls_per_caller_30m'] and short >= r['short_calls_per_caller_30m']:
            _block_caller(pc.pool, pc.caller_id, NOTE_SILENT, r['block_hours'])
    except Exception:
        logger.exception('number pool hang-up check failed for %s', getattr(call_log, 'id', ''))


def blocked_counts(pool, since) -> dict:
    """The Spam protection card: repeat and silent callers blocked, numbers
    pulled for flooding, complaints on this advertiser."""
    from dni.models import DNIComplaint, DNINumber
    from spam_protection.models import Blacklist
    base = Blacklist.objects.filter(organization_id=pool.organization_id, campaign_id=pool.campaign_id,
                                    reason=Blacklist.Reason.AUTO, notes__endswith=f'({pool.name})')
    return {
        'repeat_callers_blocked': base.filter(notes__startswith=NOTE_REPEAT).count(),
        'silent_callers_blocked': base.filter(notes__startswith=NOTE_SILENT).count(),
        'numbers_pulled_for_flooding': DNINumber.objects.filter(
            pool=pool, status=DNINumber.Status.QUARANTINED, status_reason__startswith=FLOOD_REASON).count(),
        'complaints': DNIComplaint.objects.filter(organization_id=pool.organization_id).count(),
    }
