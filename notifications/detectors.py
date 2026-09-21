"""Watches for the conditions that should raise an alert.

The alert types existed and could be switched on in settings, but nothing ever
looked for them — campaign.cap_reached, buyer.cap_reached,
destination.cap_reached, buyer.missed and aht.low were dead options.

Each detector answers one question against current data and returns the events
it found. They are deliberately read-only: they decide nothing and change
nothing, so a detector going wrong cannot affect a call.

Cap alerts fire at a threshold below the limit (default 80%) so there is warning
before calls start being refused, and again at 100%. An alert is sent once per
campaign, buyer or destination per day for a given level — checking every few
minutes would otherwise repeat the same warning all day.
"""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Fraction of a cap that counts as "filling up"
WARN_AT = 0.8

# How long a fired alert stays suppressed, so one condition does not repeat
SUPPRESS_SECONDS = 60 * 60 * 24


def _already_sent(key: str) -> bool:
    """True if this exact alert already went out inside the suppression window."""
    cache_key = f'alert_sent:{key}'
    if cache.get(cache_key):
        return True
    cache.set(cache_key, True, timeout=SUPPRESS_SECONDS)
    return False


def _level(used: int, limit: int):
    """'full', 'warn' or None for how close a count is to its cap."""
    if limit <= 0:
        return None
    if used >= limit:
        return 'full'
    if used >= limit * WARN_AT:
        return 'warn'
    return None


def _payload(kind, name, used, limit, level, period, **extra):
    return {
        'type': kind,
        'name': name,
        'used': used,
        'limit': limit,
        'percent': round((used / limit) * 100, 1) if limit else 0,
        'level': level,
        'period': period,
        **extra,
    }


# ── campaign caps ────────────────────────────────────────────────────────────

def check_campaign_caps(organization):
    from campaigns.models import Campaign
    from routing.models import CallLog

    events = []
    today = timezone.now().date()
    month_start = today.replace(day=1)

    campaigns = Campaign.objects.filter(
        organization=organization, status='active'
    ).select_related('cap')

    for campaign in campaigns:
        cap = getattr(campaign, 'cap', None)
        if cap is None:
            continue

        counts = {
            'daily': (
                CallLog.objects.filter(campaign=campaign, created_at__date=today).count(),
                cap.max_calls_daily,
            ),
            'monthly': (
                CallLog.objects.filter(campaign=campaign, created_at__date__gte=month_start).count(),
                cap.max_calls_monthly,
            ),
        }

        for period, (used, limit) in counts.items():
            level = _level(used, limit)
            if not level:
                continue
            if _already_sent(f'campaign:{campaign.id}:{period}:{level}'):
                continue
            events.append(('campaign.cap_reached', _payload(
                'campaign', campaign.name, used, limit, level, period,
                campaign_id=str(campaign.id),
            )))

    return events


# ── buyer caps ───────────────────────────────────────────────────────────────

def check_buyer_caps(organization):
    from buyers.models import Buyer
    from routing.models import CallLog

    events = []
    today = timezone.now().date()
    month_start = today.replace(day=1)

    for buyer in Buyer.objects.filter(organization=organization, status='active').select_related('cap'):
        cap = getattr(buyer, 'cap', None)
        if cap is None:
            continue

        counts = {
            'daily': (
                CallLog.objects.filter(buyer=buyer, created_at__date=today).count(),
                cap.max_calls_daily,
            ),
            'monthly': (
                CallLog.objects.filter(buyer=buyer, created_at__date__gte=month_start).count(),
                cap.max_calls_monthly,
            ),
        }

        for period, (used, limit) in counts.items():
            level = _level(used, limit)
            if not level:
                continue
            if _already_sent(f'buyer:{buyer.id}:{period}:{level}'):
                continue
            events.append(('buyer.cap_reached', _payload(
                'buyer', buyer.name, used, limit, level, period,
                buyer_id=str(buyer.id),
            )))

    return events


# ── destination caps ─────────────────────────────────────────────────────────

def check_destination_caps(organization):
    from buyers.destination import Destination

    events = []
    for dest in Destination.objects.filter(organization=organization, enabled=True).select_related('buyer'):
        counts = {
            'hourly': (dest.hourly_calls, dest.hourly_cap),
            'daily': (dest.daily_calls, dest.daily_cap),
            'monthly': (dest.monthly_calls, dest.monthly_cap),
        }
        for period, (used, limit) in counts.items():
            level = _level(used, limit)
            if not level:
                continue
            if _already_sent(f'destination:{dest.id}:{period}:{level}'):
                continue
            events.append(('destination.cap_reached', _payload(
                'destination', dest.name, used, limit, level, period,
                destination_id=str(dest.id),
                buyer=dest.buyer.name if dest.buyer_id else None,
            )))

    return events


# ── buyers missing calls ─────────────────────────────────────────────────────

def check_buyer_missed(organization, window_minutes=60, min_calls=3, miss_rate=0.5):
    """A buyer failing to answer a meaningful share of recent calls.

    Needs at least min_calls in the window before judging, so one missed call on
    a quiet line does not raise an alert.
    """
    from routing.models import CallLog

    events = []
    since = timezone.now() - timedelta(minutes=window_minutes)

    rows = (
        CallLog.objects
        .filter(organization=organization, created_at__gte=since, buyer__isnull=False)
        .select_related('buyer')
    )

    per_buyer = {}
    for call in rows:
        stat = per_buyer.setdefault(call.buyer_id, {'name': call.buyer.name, 'total': 0, 'missed': 0})
        stat['total'] += 1
        if call.status in (CallLog.Status.NO_ANSWER, CallLog.Status.BUSY, CallLog.Status.FAILED):
            stat['missed'] += 1

    for buyer_id, stat in per_buyer.items():
        if stat['total'] < min_calls:
            continue
        rate = stat['missed'] / stat['total']
        if rate < miss_rate:
            continue
        if _already_sent(f'buyer_missed:{buyer_id}'):
            continue
        events.append(('buyer.missed', {
            'type': 'buyer',
            'name': stat['name'],
            'buyer_id': str(buyer_id),
            'missed': stat['missed'],
            'total': stat['total'],
            'percent': round(rate * 100, 1),
            'window_minutes': window_minutes,
        }))

    return events


# ── average handle time dropping ─────────────────────────────────────────────

def check_low_aht(organization, window_minutes=60, min_calls=5, drop_ratio=0.6):
    """Recent answered calls much shorter than the campaign's usual.

    Compares the last window against the preceding seven days for the same
    campaign. Needs history and volume before judging, since a handful of short
    calls proves nothing.
    """
    from django.db.models import Avg, Count, Q
    from routing.models import CallLog

    events = []
    now = timezone.now()
    since = now - timedelta(minutes=window_minutes)
    baseline_start = now - timedelta(days=7)

    answered = Q(status=CallLog.Status.COMPLETED, duration__gt=0)

    recent = (
        CallLog.objects
        .filter(answered, organization=organization, created_at__gte=since, campaign__isnull=False)
        .values('campaign_id', 'campaign__name')
        .annotate(avg=Avg('duration'), n=Count('id'))
    )

    for row in recent:
        if row['n'] < min_calls:
            continue

        baseline = (
            CallLog.objects
            .filter(
                answered,
                organization=organization,
                campaign_id=row['campaign_id'],
                created_at__gte=baseline_start,
                created_at__lt=since,
            )
            .aggregate(avg=Avg('duration'))['avg']
        )
        if not baseline:
            continue

        if row['avg'] >= baseline * drop_ratio:
            continue
        if _already_sent(f'aht_low:{row["campaign_id"]}'):
            continue

        events.append(('aht.low', {
            'type': 'campaign',
            'name': row['campaign__name'],
            'campaign_id': str(row['campaign_id']),
            'recent_aht_seconds': int(row['avg']),
            'baseline_aht_seconds': int(baseline),
            'drop_percent': round((1 - row['avg'] / baseline) * 100, 1),
            'calls': row['n'],
            'window_minutes': window_minutes,
        }))

    return events


DETECTORS = (
    check_campaign_caps,
    check_buyer_caps,
    check_destination_caps,
    check_buyer_missed,
    check_low_aht,
)


def run_all(organization):
    """Run every detector. One failing must not stop the others."""
    events = []
    for detector in DETECTORS:
        try:
            events.extend(detector(organization))
        except Exception:
            logger.exception('alert detector %s failed for %s', detector.__name__, organization)
    return events
