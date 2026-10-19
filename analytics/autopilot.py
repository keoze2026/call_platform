"""Autopilot: the rules a workspace can switch on, and the engine that runs them.

Before this, only one rule did anything (pausing a buyer at 70% no-answer, and
it ran whether or not its switch was on). The volume and revenue switches were
saved and never read, the thresholds were ignored, and the settings lived in
the cache. Every rule below reads its own switch and threshold, does its job,
and says what it did.

Two kinds of rule:

  acts    pause a buyer, or resume one Autopilot paused itself
  alerts  email the workspace admins (event `autopilot.alert`)

A pass runs when someone presses "Run now", and on its own every few minutes
for workspaces that switch on "Run automatically".
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db.models import Avg, Count, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

EVENT = 'autopilot.alert'
DAY_TZ = ZoneInfo('America/New_York')   # the platform's reporting day
ALERT_QUIET_SECONDS = 6 * 60 * 60       # one alert per condition every 6 hours
HISTORY_LIMIT = 30

# name -> (default, minimum, maximum). Booleans have no range.
DEFAULTS = {
    # Acts
    'pause_on_high_no_answer': True,
    'no_answer_threshold': (70, 10, 100),
    'pause_on_short_calls': False,
    'short_call_seconds': (15, 1, 600),
    'min_calls_to_judge': (10, 3, 500),
    'resume_recovered_buyers': False,
    'resume_after_minutes': (120, 5, 1440),
    # Alerts
    'alert_on_volume_drop': True,
    'volume_drop_threshold': (50, 10, 95),
    'alert_on_revenue_drop': True,
    'revenue_drop_threshold': (40, 10, 95),
    'alert_on_low_balance': True,
    'low_balance_amount': (25, 1, 100000),
    'alert_on_no_calls': True,
    'no_calls_hours': (2, 1, 12),
    'alert_on_high_spam': True,
    'spam_rate_threshold': (30, 5, 100),
    'alert_on_campaign_without_buyers': True,
    # Schedule
    'auto_run': False,
    'run_every_minutes': (15, 5, 1440),
}


def default_options() -> dict:
    return {k: (v[0] if isinstance(v, tuple) else v) for k, v in DEFAULTS.items()}


def clean(patch: dict) -> dict:
    """Keep only known options, with numbers held inside their allowed range."""
    out = {}
    for key, value in (patch or {}).items():
        spec = DEFAULTS.get(key)
        if spec is None or value is None:
            continue
        if isinstance(spec, tuple):
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            out[key] = max(spec[1], min(spec[2], number))
        else:
            out[key] = bool(value)
    return out


def get_settings(organization):
    from analytics.models import AutopilotSettings
    obj, _ = AutopilotSettings.objects.get_or_create(organization=organization)
    return obj


def options_for(obj) -> dict:
    merged = default_options()
    merged.update(clean(obj.options))
    return merged


def update_options(organization, patch: dict) -> dict:
    """Change only the options sent; every other switch keeps its value."""
    obj = get_settings(organization)
    obj.options = {**(obj.options or {}), **clean(patch)}
    obj.save(update_fields=['options', 'updated_at'])
    return options_for(obj)


# ── helpers ──────────────────────────────────────────────────────────────────

def _action(action, entity, reason):
    return {'action': action, 'entity': entity, 'reason': reason}


def _day_start(now):
    local = now.astimezone(DAY_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _alert(organization, kind, title, message, extra=None):
    """Email the admins once per condition per quiet period."""
    key = f'autopilot_alert:{organization.id}:{kind}'
    if cache.get(key):
        return _action('still_active', title, 'Already alerted in the last 6 hours.')
    cache.set(key, True, timeout=ALERT_QUIET_SECONDS)
    try:
        from notifications.defaults import ensure_default_rules
        from notifications.services import NotificationService
        ensure_default_rules(organization)
        NotificationService.dispatch(EVENT, organization, {
            'type': kind, 'name': title, 'message': message, **(extra or {}),
        })
    except Exception:
        logger.exception('autopilot could not send the %s alert for %s', kind, organization.id)
    return _action('alert_sent', title, message)


def _same_time_baseline(model_qs, now, field, days=7):
    """Average over the previous `days` days of the same stretch of the day.

    Today so far (midnight to now, Eastern) is compared with midnight to the
    same time on each of the days before it. Comparing a half-finished day with
    a whole yesterday made every morning look like a collapse.
    """
    start_today = _day_start(now)
    elapsed = now - start_today
    totals = []
    for i in range(1, days + 1):
        start = start_today - timedelta(days=i)
        end = start + elapsed
        qs = model_qs.filter(created_at__gte=start, created_at__lt=end)
        totals.append(qs.aggregate(v=Sum(field))['v'] if field else qs.count())
    totals = [Decimal(t or 0) for t in totals]
    return (sum(totals) / Decimal(len(totals))) if totals else Decimal(0)


# ── the rules ────────────────────────────────────────────────────────────────

def _judge_buyers(organization, opts, state, now):
    """Pause buyers that are not answering, or answering and hanging up."""
    from buyers.models import Buyer
    from routing.models import CallLog

    actions = []
    window_start = now - timedelta(hours=24)
    minimum = opts['min_calls_to_judge']
    resumed_at = state.setdefault('resumed_at', {})
    paused = state.setdefault('paused_buyers', {})

    for buyer in Buyer.objects.filter(organization=organization, status=Buyer.Status.ACTIVE):
        since = window_start
        # Calls from before a resume are the reason it was paused. Judging them
        # again would pause it the minute it came back.
        resumed = resumed_at.get(str(buyer.id))
        if resumed:
            since = max(since, datetime.fromisoformat(resumed))
        logs = CallLog.objects.filter(buyer=buyer, created_at__gte=since)

        reason = None
        if opts['pause_on_high_no_answer']:
            total = logs.count()
            if total >= minimum:
                missed = logs.filter(status__in=['no_answer', 'busy']).count()
                rate = missed * 100 / total
                if rate > opts['no_answer_threshold']:
                    reason = (f"No-answer rate {rate:.0f}% over {total} calls exceeded "
                              f"{opts['no_answer_threshold']}%")
        if reason is None and opts['pause_on_short_calls']:
            done = logs.filter(status='completed')
            count = done.count()
            if count >= minimum:
                avg = done.aggregate(a=Avg('duration'))['a'] or 0
                if avg < opts['short_call_seconds']:
                    reason = (f"Average call length {avg:.0f}s over {count} answered calls "
                              f"is under {opts['short_call_seconds']}s")
        if reason:
            buyer.status = Buyer.Status.PAUSED
            buyer.save(update_fields=['status', 'updated_at'])
            paused[str(buyer.id)] = now.isoformat()
            resumed_at.pop(str(buyer.id), None)
            actions.append(_action('paused_buyer', buyer.name, reason))
    return actions


def _resume_buyers(organization, opts, state, now):
    """Bring back buyers Autopilot paused, once the wait is over."""
    from buyers.models import Buyer

    actions = []
    paused = state.setdefault('paused_buyers', {})
    resumed_at = state.setdefault('resumed_at', {})
    wait = timedelta(minutes=opts['resume_after_minutes'])

    for buyer_id, when in list(paused.items()):
        buyer = Buyer.objects.filter(organization=organization, id=buyer_id).first()
        # Gone, or someone already changed it by hand: no longer ours to touch.
        if buyer is None or buyer.status != Buyer.Status.PAUSED:
            paused.pop(buyer_id, None)
            continue
        if not opts['resume_recovered_buyers']:
            continue
        if now - datetime.fromisoformat(when) < wait:
            continue
        buyer.status = Buyer.Status.ACTIVE
        buyer.save(update_fields=['status', 'updated_at'])
        paused.pop(buyer_id, None)
        resumed_at[buyer_id] = now.isoformat()
        actions.append(_action(
            'resumed_buyer', buyer.name,
            f"Back on after {opts['resume_after_minutes']} minutes. It is paused again if it keeps failing.",
        ))
    return actions


def _volume_and_revenue(organization, opts, now):
    from routing.models import CallLog

    actions = []
    start_today = _day_start(now)
    # The first hours of the day are too thin to compare.
    if now - start_today < timedelta(hours=3):
        return actions

    calls = CallLog.objects.filter(organization=organization)
    today = calls.filter(created_at__gte=start_today, created_at__lte=now)

    if opts['alert_on_volume_drop']:
        base = _same_time_baseline(calls, now, None)
        if base >= 10:
            current = today.count()
            drop = float((base - current) * 100 / base)
            if drop > opts['volume_drop_threshold']:
                actions.append(_alert(
                    organization, 'volume_drop', f'Call volume down {drop:.0f}%',
                    f'{current} calls so far today against {base:.0f} on average at this time '
                    f'over the last 7 days.',
                    {'metric': 'call_volume', 'value': current, 'baseline': round(float(base), 1)},
                ))
    if opts['alert_on_revenue_drop']:
        base = _same_time_baseline(calls, now, 'revenue')
        if base >= 10:
            current = Decimal(today.aggregate(v=Sum('revenue'))['v'] or 0)
            drop = float((base - current) * 100 / base)
            if drop > opts['revenue_drop_threshold']:
                actions.append(_alert(
                    organization, 'revenue_drop', f'Revenue down {drop:.0f}%',
                    f'${current:.2f} so far today against ${base:.2f} on average at this time '
                    f'over the last 7 days.',
                    {'metric': 'revenue', 'value': str(current), 'baseline': str(round(base, 2))},
                ))
    return actions


def _balance(organization, opts):
    from billing.models import BillingAccount

    account = BillingAccount.objects.filter(organization=organization).first()
    if account is None:
        return []
    balance = Decimal(account.balance or 0)
    if balance <= Decimal(opts['low_balance_amount']):
        return [_alert(
            organization, 'low_balance', f'Balance is ${balance:.2f}',
            f"The balance is at or under your ${opts['low_balance_amount']} Autopilot limit. "
            f"Calls stop when it runs out.",
            {'metric': 'balance', 'value': str(balance)},
        )]
    return []


def _no_calls(organization, opts, now):
    from routing.models import CallLog

    local = now.astimezone(DAY_TZ)
    if not 8 <= local.hour < 21:
        return []
    hours = opts['no_calls_hours']
    since = now - timedelta(hours=hours)
    calls = CallLog.objects.filter(organization=organization)
    # Only a workspace that was already getting calls today can have "gone quiet".
    started_today = calls.filter(created_at__gte=_day_start(now), created_at__lt=since).exists()
    if started_today and not calls.filter(created_at__gte=since).exists():
        return [_alert(
            organization, 'no_calls', f'No calls in {hours} hour{"s" if hours != 1 else ""}',
            f'Calls came in earlier today but none in the last {hours} '
            f'hour{"s" if hours != 1 else ""}. Check the numbers and the carrier.',
            {'metric': 'live_activity', 'value': 0},
        )]
    return []


def _spam(organization, opts, now):
    from routing.models import CallLog

    today = CallLog.objects.filter(organization=organization, created_at__gte=_day_start(now))
    total = today.count()
    if total < 10:
        return []
    blocked = today.filter(status='failed', ipqs_checked=True).count()
    rate = blocked * 100 / total
    if rate > opts['spam_rate_threshold']:
        return [_alert(
            organization, 'high_spam', f'{rate:.0f}% of calls blocked as spam',
            f'{blocked} of {total} calls today were blocked by the spam checks, '
            f"over your {opts['spam_rate_threshold']}% limit.",
            {'metric': 'spam_rate', 'value': round(rate, 1)},
        )]
    return []


def _campaigns_without_buyers(organization):
    from campaigns.models import Campaign

    actions = []
    for campaign in Campaign.objects.filter(organization=organization, status='active'):
        if not campaign.campaign_assignments.filter(is_active=True).exists():
            actions.append(_alert(
                organization, f'no_buyers:{campaign.id}',
                f'{campaign.name} has no active buyer',
                f'Calls to {campaign.name} will fail because no buyer is assigned to it.',
                {'metric': 'campaign', 'campaign': campaign.name},
            ))
    return actions


# ── a pass ───────────────────────────────────────────────────────────────────

def run_pass(organization, source='manual') -> list:
    """Run every switched-on rule once. Returns what it did."""
    obj = get_settings(organization)
    opts = options_for(obj)
    now = timezone.now()
    state = obj.state or {}
    actions = []

    for step in (
        lambda: _resume_buyers(organization, opts, state, now),
        lambda: _judge_buyers(organization, opts, state, now)
        if (opts['pause_on_high_no_answer'] or opts['pause_on_short_calls']) else [],
        lambda: _volume_and_revenue(organization, opts, now),
        lambda: _balance(organization, opts) if opts['alert_on_low_balance'] else [],
        lambda: _no_calls(organization, opts, now) if opts['alert_on_no_calls'] else [],
        lambda: _spam(organization, opts, now) if opts['alert_on_high_spam'] else [],
        lambda: _campaigns_without_buyers(organization)
        if opts['alert_on_campaign_without_buyers'] else [],
    ):
        # One rule failing must not stop the others.
        try:
            actions.extend(step())
        except Exception:
            logger.exception('autopilot rule failed for %s', organization.id)

    done = [a for a in actions if a['action'] != 'still_active']
    stamp = now.isoformat()
    obj.state = state
    obj.last_run_at = now
    obj.history = ([{**a, 'at': stamp, 'source': source} for a in done]
                   + list(obj.history or []))[:HISTORY_LIMIT]
    obj.save(update_fields=['state', 'last_run_at', 'history', 'updated_at'])
    return actions


def run_due_passes() -> int:
    """For the scheduler: run each workspace that asked to run automatically."""
    from analytics.models import AutopilotSettings

    ran = 0
    now = timezone.now()
    for obj in AutopilotSettings.objects.select_related('organization').filter(
            organization__is_active=True):
        opts = options_for(obj)
        if not opts['auto_run']:
            continue
        gap = timedelta(minutes=opts['run_every_minutes'])
        if obj.last_run_at and now - obj.last_run_at < gap - timedelta(seconds=30):
            continue
        run_pass(obj.organization, source='auto')
        ran += 1
    return ran
