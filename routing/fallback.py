"""Routing on Server A while Server B is down (CH-464, Sir 2 Nov).

Sir's conditions for the server move: Server A (Asterisk) keeps routing calls
when Server B (portal, database) cannot be reached; buyer caps and
concurrency limits still apply; every call made meanwhile is recorded on B
afterwards, so billing stays accurate.

Two endpoints, both behind the Asterisk shared secret, both new - nothing on
the live routing path changes:

  GET  /api/twilio/asterisk/fallback-plan/
       Everything A needs to route on its own, read every minute by
       scripts/fallback/fallback_sync.py: each active number and its campaign,
       the campaign's rules and destinations, buyers and their lines, every
       cap with how much of it is used, the blocked and allowed callers.

  GET  /api/twilio/asterisk/fallback-plan-stamp/
       CH-471: a fingerprint of the plan's on/off settings only (numbers,
       campaigns, buyers, lines, rules, caps' limits). A asks for it every
       5 seconds and fetches the plan at once when it differs from the plan
       it holds, so a pause or play in the portal reaches A within seconds,
       not at the next minute.

  POST /api/twilio/asterisk/fallback-calls/
       The calls A routed (or refused) on its own, sent once B answers again.
       Each becomes the call row B would have written, with A's call id, so
       the hang-up reports that waited on A (call_ended.sh) find their call
       and bill it exactly as usual.

The decisions themselves are made on A by scripts/fallback/avx_fallback.py,
which follows routing/engine.py and the route endpoint step by step.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from routing.models import CallLog, RoutingRule

logger = logging.getLogger(__name__)

PLAN_VERSION = 1
MAX_CALLS_PER_POST = 500


def _epoch(dt):
    return dt.timestamp() if dt is not None else None


def _next_midnight(day_start):
    """The next local midnight after `day_start` (a local midnight)."""
    nxt = (day_start + timedelta(days=1, hours=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt


def _next_month(day_start):
    first = day_start.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return nxt


def _cap(limit, used, start, nxt, rolling=False):
    return {'limit': int(limit), 'used': int(used), 'start': _epoch(start),
            'next': _epoch(nxt), 'rolling': bool(rolling)}


# ── the plan ─────────────────────────────────────────────────────────────────

def _campaign_caps(campaign, now):
    """check_campaign_caps, as numbers: the limits and how far into each."""
    try:
        cap = campaign.cap
    except Exception:
        return {}, 0
    out = {}
    dispatched = (CallLog.objects.filter(campaign=campaign, block_reason='')
                  .exclude(destination_number='').exclude(destination_number__isnull=True))
    day = now.astimezone(dt_timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    if cap.max_calls_daily > 0:
        out['daily'] = _cap(cap.max_calls_daily, dispatched.filter(created_at__date=day.date()).count(),
                            day, _next_midnight(day))
    if cap.max_calls_monthly > 0:
        out['monthly'] = _cap(cap.max_calls_monthly,
                              dispatched.filter(created_at__date__gte=month.date()).count(),
                              month, _next_month(day))
    if cap.max_calls_global > 0:
        out['global'] = _cap(cap.max_calls_global, dispatched.count(), None, None)
    return out, max(0, cap.max_concurrency or 0)


def _buyer_caps(buyer, now):
    """buyers.caps.buyer_cap_hit, as numbers."""
    from buyers.caps import DEFAULT_TZ, cap_q, windows
    try:
        cap = buyer.cap
    except Exception:
        return {}
    limits = (('daily', cap.max_calls_daily), ('monthly', cap.max_calls_monthly))
    if not any((c or 0) > 0 for _, c in limits):
        return {}
    _, day_start, month_start = windows(getattr(buyer, 'timezone', '') or DEFAULT_TZ, now)
    base = CallLog.objects.filter(buyer=buyer).exclude(destination_number='').filter(cap_q())
    out = {}
    if (cap.max_calls_daily or 0) > 0:
        out['daily'] = _cap(cap.max_calls_daily, base.filter(created_at__gte=day_start).count(),
                            day_start, _next_midnight(day_start))
    if (cap.max_calls_monthly or 0) > 0:
        out['monthly'] = _cap(cap.max_calls_monthly, base.filter(created_at__gte=month_start).count(),
                              month_start, _next_month(day_start))
    return out


def _destination_caps(d, now):
    """buyers.caps.destination_cap_hit, as numbers."""
    from buyers.caps import cap_q, own_calls_q, windows
    caps = (('hourly', d.hourly_cap), ('daily', d.daily_cap),
            ('monthly', d.monthly_cap), ('global', d.global_cap))
    if not any((c or 0) > 0 for _, c in caps) or not d.tfn:
        return {}
    hour_ago, day_start, month_start = windows(d.timezone, now)
    base = CallLog.objects.filter(organization_id=d.organization_id).filter(own_calls_q(d)).filter(cap_q())
    out = {}
    if (d.hourly_cap or 0) > 0:
        out['hourly'] = _cap(d.hourly_cap, base.filter(created_at__gte=hour_ago).count(),
                             hour_ago, now + timedelta(hours=1), rolling=True)
    if (d.daily_cap or 0) > 0:
        out['daily'] = _cap(d.daily_cap, base.filter(created_at__gte=day_start).count(),
                            day_start, _next_midnight(day_start))
    if (d.monthly_cap or 0) > 0:
        out['monthly'] = _cap(d.monthly_cap, base.filter(created_at__gte=month_start).count(),
                              month_start, _next_month(day_start))
    if (d.global_cap or 0) > 0:
        out['global'] = _cap(d.global_cap, base.count(), None, None)
    return out


def _balance_ok(campaign, phone, memo):
    """RoutingEngine.check_balance, asked once per campaign and number price."""
    from routing.engine import RoutingEngine
    key = (campaign.id, str(getattr(phone, 'payout_per_call', '') or ''))
    if key not in memo:
        try:
            memo[key] = bool(RoutingEngine.check_balance(campaign, phone))
        except Exception:
            # The live check fails open on an error; so does the plan.
            logger.exception('fallback plan: balance check failed for campaign %s', campaign.id)
            memo[key] = True
    return memo[key]


# CH-471: the settings a pause, play or edit in the portal changes, model by
# model. Only switches and limits: nothing that moves with every call (cap
# usage, balances), so the fingerprint stays still while calls run and A
# fetches the plan only when someone changed something. Blocked and allowed
# callers stay on the one-minute refresh.
STAMP_FIELDS = (
    ('phone_numbers.models', 'PhoneNumber', ('number', 'status', 'campaign_id', 'publisher_id')),
    ('campaigns.models', 'Campaign', ('id', 'organization_id', 'status', 'routing_type', 'duplicate_call_block',
                                      'duplicate_call_block_hours', 'duplicate_handling', 'duplicate_direction',
                                      'recording_enabled')),
    ('campaigns.models', 'CampaignCap', ('campaign_id', 'max_calls_daily', 'max_calls_monthly',
                                         'max_calls_global', 'max_concurrency')),
    ('buyers.models', 'Buyer', ('id', 'status', 'max_concurrency')),
    ('buyers.models', 'BuyerCap', ('buyer_id', 'max_calls_daily', 'max_calls_monthly',
                                   'max_calls_global', 'max_concurrency')),
    ('buyers.destination', 'Destination', ('id', 'buyer_id', 'tfn', 'enabled', 'concurrency_cap', 'hourly_cap',
                                           'daily_cap', 'monthly_cap', 'global_cap')),
    ('routing.models', 'RoutingRule', ('id', 'campaign_id', 'name', 'status', 'rule_type', 'priority')),
    ('routing.models', 'RuleCondition', ('id', 'rule_id', 'conditions')),
    ('routing.models', 'RuleDestination', ('id', 'rule_id', 'destination_type', 'destination', 'buyer_id',
                                           'priority', 'weight')),
    ('spam_protection.models', 'AnonymousCallBlock', ('id', 'campaign_id', 'is_active')),
)


def plan_stamp():
    """A short fingerprint of every on/off setting the plan carries."""
    import importlib
    h = hashlib.sha1()
    for module, name, fields in STAMP_FIELDS:
        model = getattr(importlib.import_module(module), name)
        have = {f.attname for f in model._meta.concrete_fields}
        cols = [f for f in fields if f in have]
        h.update(f'{name}:{",".join(cols)}\n'.encode())
        for row in model.objects.order_by('pk').values_list(*cols):
            h.update(json.dumps(row, sort_keys=True, default=str).encode())
            h.update(b'\n')
    return h.hexdigest()


def build_plan(now=None):
    """Everything Server A needs to route a call while B is away."""
    from buyers.destination import Destination
    from buyers.models import Buyer
    from campaigns.models import Campaign
    from phone_numbers.models import PhoneNumber
    from spam_protection.models import AnonymousCallBlock, Blacklist, Whitelist

    now = now or timezone.now()
    stamp = plan_stamp()   # first: a change made while the plan is built shows as a new stamp
    phones = list(PhoneNumber.objects.filter(status='active', campaign__isnull=False)
                  .select_related('campaign'))
    campaign_ids = {p.campaign_id for p in phones}
    campaigns = {c.id: c for c in Campaign.objects.filter(id__in=campaign_ids).select_related('organization')}

    balance_memo = {}
    numbers = {}
    for p in phones:
        c = campaigns.get(p.campaign_id)
        if c is None:
            continue
        numbers[p.number] = {
            'campaign_id': str(c.id),
            'organization_id': str(c.organization_id),
            'publisher_id': str(p.publisher_id) if p.publisher_id else None,
            'balance_ok': _balance_ok(c, p, balance_memo) if c.status == 'active' else True,
        }

    anon = set(AnonymousCallBlock.objects.filter(campaign_id__in=campaign_ids, is_active=True)
               .values_list('campaign_id', flat=True))

    rules = (RoutingRule.objects.filter(campaign_id__in=campaign_ids, status=RoutingRule.Status.ACTIVE)
             .prefetch_related('destinations').order_by('priority'))
    rules_by_campaign = {}
    buyer_ids = set()
    for r in rules:
        first = r.conditions.first()
        dests = sorted(r.destinations.all(), key=lambda d: (d.priority, d.created_at))
        rules_by_campaign.setdefault(r.campaign_id, []).append({
            'id': str(r.id),
            'name': r.name,
            'type': r.rule_type,
            'priority': r.priority,
            'conditions': first.conditions if first is not None and isinstance(first.conditions, dict) else None,
            'destinations': [{
                'id': str(d.id),
                'destination': d.destination,
                'type': d.destination_type,
                'buyer_id': str(d.buyer_id) if d.buyer_id else None,
                'priority': d.priority,
                'weight': d.weight,
            } for d in dests],
        })
        buyer_ids |= {d.buyer_id for d in dests if d.buyer_id}

    out_campaigns = {}
    for cid, c in campaigns.items():
        caps, cc = _campaign_caps(c, now)
        out_campaigns[str(cid)] = {
            'name': c.name,
            'organization_id': str(c.organization_id),
            'status': c.status,
            'routing_type': c.routing_type,
            'duplicate_call_block': bool(c.duplicate_call_block),
            'duplicate_call_block_hours': c.duplicate_call_block_hours or 0,
            'duplicate_handling': getattr(c, 'duplicate_handling', 'normal') or 'normal',
            'duplicate_direction': getattr(c, 'duplicate_direction', 'destination') or 'destination',
            'recording_enabled': bool(getattr(c, 'recording_enabled', True)),
            'anonymous_block': cid in anon,
            'caps': caps,
            'max_concurrency': cc,
            'rules': rules_by_campaign.get(cid, []),
        }

    out_buyers = {}
    lines = {}
    for d in Destination.objects.filter(buyer_id__in=buyer_ids).order_by('created_at'):
        lines.setdefault(d.buyer_id, []).append(d)
    for b in Buyer.objects.filter(id__in=buyer_ids).select_related('cap'):
        out_buyers[str(b.id)] = {
            'name': b.name,
            'status': b.status,
            'max_concurrency': max(0, b.max_concurrency or 0),
            'caps': _buyer_caps(b, now),
            'lines': [{
                'tfn': d.tfn,
                'enabled': bool(d.enabled),
                'concurrency_cap': max(0, d.concurrency_cap or 0),
                'caps': _destination_caps(d, now),
            } for d in lines.get(b.id, [])],
        }

    org_ids = {c.organization_id for c in campaigns.values()}
    blocks = [{
        'number': row['phone_number'],
        'organization_id': str(row['organization_id']),
        'campaign_id': str(row['campaign_id']) if row['campaign_id'] else None,
        'buyer_id': str(row['buyer_id']) if row['buyer_id'] else None,
        'publisher_id': str(row['publisher_id']) if row['publisher_id'] else None,
        'reason': row['reason'] or '',
        'expires_at': _epoch(row['expires_at']),
    } for row in Blacklist.objects.filter(organization_id__in=org_ids, is_active=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .values('phone_number', 'organization_id', 'campaign_id', 'buyer_id',
                'publisher_id', 'reason', 'expires_at')]
    allows = [{
        'number': row['phone_number'],
        'organization_id': str(row['organization_id']),
        'campaign_id': str(row['campaign_id']) if row['campaign_id'] else None,
    } for row in Whitelist.objects.filter(organization_id__in=org_ids, is_active=True)
        .values('phone_number', 'organization_id', 'campaign_id')]

    from routing.engine import RoutingEngine
    area_states = {}
    for code in range(200, 1000):
        state = RoutingEngine.get_caller_state(str(code))
        if state != 'Unknown':
            area_states[str(code)] = state

    return {
        'version': PLAN_VERSION,
        'generated_at': now.timestamp(),
        'stamp': stamp,
        'area_states': area_states,
        'numbers': numbers,
        'campaigns': out_campaigns,
        'buyers': out_buyers,
        'blocks': blocks,
        'allows': allows,
    }


@csrf_exempt
@require_http_methods(["GET"])
def fallback_plan(request):
    from routing.asterisk_handler import _check_secret
    if not _check_secret(request):
        return JsonResponse({"error": "Forbidden"}, status=403)
    try:
        return JsonResponse(build_plan())
    except Exception:
        logger.exception('fallback plan could not be built')
        return JsonResponse({"error": "plan failed"}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def fallback_plan_stamp(request):
    """CH-471: A asks every 5 seconds; a new stamp means fetch the plan now."""
    from routing.asterisk_handler import _check_secret
    if not _check_secret(request):
        return JsonResponse({"error": "Forbidden"}, status=403)
    try:
        return JsonResponse({'stamp': plan_stamp()})
    except Exception:
        logger.exception('fallback plan stamp could not be worked out')
        return JsonResponse({"error": "stamp failed"}, status=500)


# ── calls A routed on its own ────────────────────────────────────────────────

def _as_dt(epoch):
    try:
        return datetime.fromtimestamp(float(epoch), tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _get(model, pk):
    """The row with this id, or None for a missing, empty or malformed id."""
    if not pk:
        return None
    try:
        return model.objects.filter(pk=pk).first()
    except (ValueError, TypeError, ValidationError):
        return None


def _set_aside_phantom(sid, keep_id, at):
    """B answered the route request too late: its row describes a call that
    was never placed (A had already routed it). Hidden like a sender's retry,
    so reports and caps count the call once."""
    for row in CallLog.objects.filter(twilio_call_sid=sid).exclude(pk=keep_id):
        if row.status in (CallLog.Status.RINGING, CallLog.Status.IN_PROGRESS):
            row.status = CallLog.Status.FAILED
            row.ended_at = row.ended_at or at
        row.retry_hidden = True
        row.block_reason = (row.block_reason or 'Routed by Server A')[:100]
        row.save(update_fields=['status', 'ended_at', 'retry_hidden', 'block_reason', 'updated_at'])
        logger.warning('fallback sync: call %s was also routed by A as %s; B row hidden', row.id, keep_id)
        return row.id
    return None


def record_call(entry):
    """One call A decided on its own, written the way the route endpoint writes
    it. Returns 'created', 'exists' or 'skipped: why'."""
    from campaigns.models import Campaign
    from routing.asterisk_handler import (NO_DESTINATION_LABEL, _rule_destination_for,
                                          normalize_caller)
    from routing.engine import RoutingEngine

    try:
        call_id = uuid.UUID(str(entry.get('id')))
    except (TypeError, ValueError):
        return 'skipped: bad id'
    if CallLog.objects.filter(pk=call_id).exists():
        return 'exists'

    created_at = _as_dt(entry.get('created_at')) or timezone.now()
    if created_at > timezone.now() + timedelta(minutes=1):
        created_at = timezone.now()
    campaign = _get(Campaign, entry.get('campaign_id'))
    if campaign is None:
        return 'skipped: campaign not found'

    caller = normalize_caller(entry.get('caller') or '')
    called = str(entry.get('called') or '')[:20]
    if not caller or not called:
        return 'skipped: missing numbers'

    digits = ''.join(c for c in caller if c.isdigit())
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    area_code = digits[:3] if len(digits) == 10 else ''
    caller_state = RoutingEngine.get_caller_state(area_code) if area_code else ''

    window = campaign.duplicate_call_block_hours or 24
    is_duplicate = window > 0 and CallLog.objects.filter(
        campaign_id=campaign.id, caller_number=caller, retry_hidden=False,
        created_at__gte=created_at - timedelta(hours=window), created_at__lt=created_at,
    ).exists()

    sid = str(entry.get('uniqueid') or '')[:100] or f'fallback-{call_id}'
    if CallLog.objects.filter(twilio_call_sid=sid).exists():
        _set_aside_phantom(sid, call_id, created_at)
        sid = f'{sid}-a'[:100]
        if CallLog.objects.filter(twilio_call_sid=sid).exists():
            sid = f'fallback-{call_id}'

    from buyers.models import Buyer
    from publishers.models import Publisher
    from routing.models import RuleDestination

    def _buyer(bid):
        return _get(Buyer, bid)

    publisher = _get(Publisher, entry.get('publisher_id'))

    trace = entry.get('trace') if isinstance(entry.get('trace'), dict) else {}
    trace = dict(trace, routed_by='Server A (Server B unreachable)')

    row = CallLog(
        id=call_id,
        organization=campaign.organization,
        campaign=campaign,
        publisher=publisher,
        publisher_payout=0,
        caller_number=caller,
        called_number=called,
        caller_area_code=area_code,
        caller_state=caller_state,
        caller_country='US' if area_code else '',
        twilio_call_sid=sid,
        is_duplicate=is_duplicate,
        routing_trace=trace,
    )
    refused = entry.get('outcome') != 'dial'
    if refused:
        reason = str(entry.get('error') or 'no_destination')
        if reason in ('Duplicate call', RoutingEngine.INSUFFICIENT_FUNDS_LABEL):
            row.status = CallLog.Status.NO_ANSWER
        elif reason == RoutingEngine.BLOCKED_CALLER_LABEL and not entry.get('auto_block'):
            row.status = CallLog.Status.NO_ANSWER
        elif reason.startswith('Destination missed'):
            # CH-519: no line got the call - no line or buyer on it (Not
            # forwarded), the same as Server B writes it.
            row.status = CallLog.Status.NO_ANSWER
        else:
            row.status = CallLog.Status.FAILED
        row.block_reason = (NO_DESTINATION_LABEL if reason.startswith('Destination missed') else reason)[:100]
        row.ended_at = created_at
    else:
        rule = _get(RoutingRule, entry.get('rule_id'))
        dest = str(entry.get('destination') or '')[:20]
        row.status = CallLog.Status.IN_PROGRESS
        row.buyer = _buyer(entry.get('buyer_id'))
        row.destination_number = dest
        row.routing_rule = rule
        rd = _get(RuleDestination, entry.get('rule_destination_id'))
        rd_id = rd.pk if rd is not None and rule is not None and rd.rule_id == rule.pk else None
        row.rule_destination_id = _rule_destination_for(rule, dest, rd_id) if rule is not None else None

    try:
        with transaction.atomic():
            row.save(force_insert=True)
            CallLog.objects.filter(pk=row.pk).update(created_at=created_at)
    except IntegrityError:
        if CallLog.objects.filter(pk=call_id).exists():
            return 'exists'
        raise

    try:
        from tasks import enrich_call_carrier
        enrich_call_carrier.delay(str(row.id), caller)
    except Exception:
        logger.warning('carrier_enrichment_not_queued: call_log=%s', row.id)
    if refused:
        try:
            from tasks import charge_rejected_call
            charge_rejected_call.delay(str(row.id))
        except Exception:
            logger.warning('rejected_fee_not_queued: call_log=%s', row.id)
    return 'created'


@csrf_exempt
@require_http_methods(["POST"])
def fallback_calls(request):
    from routing.asterisk_handler import _check_secret
    if not _check_secret(request):
        return JsonResponse({"error": "Forbidden"}, status=403)
    try:
        data = json.loads(request.body.decode('utf-8'))
        calls = data.get('calls') or []
        if not isinstance(calls, list):
            raise ValueError
    except Exception:
        return JsonResponse({"error": "Bad JSON"}, status=400)
    if len(calls) > MAX_CALLS_PER_POST:
        return JsonResponse({"error": f"at most {MAX_CALLS_PER_POST} calls per post"}, status=400)
    results = {}
    for entry in calls:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get('id'))
        try:
            results[key] = record_call(entry)
        except Exception:
            logger.exception('fallback sync: call %s not recorded', key)
            results[key] = 'error'
    return JsonResponse({"results": results})
