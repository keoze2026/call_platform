from django.db.models import (
    Count, Sum, Avg, Q, F,
    FloatField, DecimalField
)
from django.db.models.functions import (
    TruncDay, TruncHour, TruncWeek, TruncMonth, Coalesce
)
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import timedelta, datetime
from decimal import Decimal
import csv
import io

from .models import CallRecord
from accounts.models import User


class AnalyticsService:

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _base_qs(user: User, filters):
        qs = CallRecord.objects.filter(organization=user.organization)

        if filters.date_from:
            dt = parse_datetime(filters.date_from + 'T00:00:00') or datetime.fromisoformat(filters.date_from)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            qs = qs.filter(created_at__gte=dt)

        if filters.date_to:
            dt = parse_datetime(filters.date_to + 'T23:59:59') or datetime.fromisoformat(filters.date_to)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            qs = qs.filter(created_at__lte=dt)

        if filters.campaign_id:
            qs = qs.filter(campaign_id=filters.campaign_id)

        if filters.buyer_id:
            qs = qs.filter(buyer_id=filters.buyer_id)

        if filters.publisher_id:
            qs = qs.filter(publisher_id=filters.publisher_id)

        if filters.status:
            qs = qs.filter(status=filters.status)

        return qs

    @staticmethod
    def _zero_decimal():
        return Decimal('0.0000')

    # ── dashboard ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_dashboard(user: User) -> dict:
        org = user.organization
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        all_qs = CallRecord.objects.filter(organization=org)

        agg = all_qs.aggregate(
            total_calls=Count('id'),
            completed=Count('id', filter=Q(status='completed')),
            converted=Count('id', filter=Q(is_converted=True)),
            spam=Count('id', filter=Q(is_spam=True)),
            duplicates=Count('id', filter=Q(is_duplicate=True)),
            total_revenue=Coalesce(Sum('revenue'), Decimal('0')),
            total_payout=Coalesce(Sum('payout'), Decimal('0')),
            total_profit=Coalesce(Sum('profit'), Decimal('0')),
            avg_duration=Coalesce(Avg('duration_seconds'), 0.0),
        )

        calls_today = all_qs.filter(created_at__gte=today_start).count()
        live_calls  = all_qs.filter(status='in_progress').count()

        total = agg['total_calls'] or 1
        return {
            'total_calls':       agg['total_calls'],
            'calls_today':       calls_today,
            'live_calls':        live_calls,
            'completed_calls':   agg['completed'],
            'converted_calls':   agg['converted'],
            'conversion_rate':   round((agg['converted'] / total) * 100, 2),
            'total_revenue':     agg['total_revenue'],
            'total_payout':      agg['total_payout'],
            'total_profit':      agg['total_profit'],
            'avg_call_duration': round(agg['avg_duration'] or 0, 1),
            'spam_blocked':      agg['spam'],
            'duplicate_blocked': agg['duplicates'],
        }

    # ── time series ──────────────────────────────────────────────────────────

    @staticmethod
    def get_time_series(user: User, filters) -> list:
        qs = AnalyticsService._base_qs(user, filters)

        trunc_map = {
            'hour':  TruncHour,
            'day':   TruncDay,
            'week':  TruncWeek,
            'month': TruncMonth,
        }
        trunc_fn = trunc_map.get(filters.granularity or 'day', TruncDay)

        rows = (
            qs
            .annotate(period=trunc_fn('created_at'))
            .values('period')
            .annotate(
                calls=Count('id'),
                converted=Count('id', filter=Q(is_converted=True)),
                revenue=Coalesce(Sum('revenue'), Decimal('0')),
                payout=Coalesce(Sum('payout'), Decimal('0')),
                profit=Coalesce(Sum('profit'), Decimal('0')),
                avg_duration=Coalesce(Avg('duration_seconds'), 0.0),
            )
            .order_by('period')
        )

        return [
            {
                'period':       r['period'].isoformat() if r['period'] else '',
                'calls':        r['calls'],
                'converted':    r['converted'],
                'revenue':      r['revenue'],
                'payout':       r['payout'],
                'profit':       r['profit'],
                'avg_duration': round(r['avg_duration'] or 0, 1),
            }
            for r in rows
        ]

    # ── campaign performance ─────────────────────────────────────────────────

    @staticmethod
    def get_campaign_performance(user: User, filters) -> list:
        qs = AnalyticsService._base_qs(user, filters)

        rows = (
            qs
            .exclude(campaign_id=None)
            .values('campaign_id', 'campaign_name')
            .annotate(
                total_calls=Count('id'),
                converted_calls=Count('id', filter=Q(is_converted=True)),
                total_revenue=Coalesce(Sum('revenue'), Decimal('0')),
                total_payout=Coalesce(Sum('payout'), Decimal('0')),
                total_profit=Coalesce(Sum('profit'), Decimal('0')),
                avg_duration=Coalesce(Avg('duration_seconds'), 0.0),
                spam_blocked=Count('id', filter=Q(is_spam=True)),
            )
            .order_by('-total_calls')
        )

        result = []
        for r in rows:
            total = r['total_calls'] or 1
            result.append({
                'campaign_id':     str(r['campaign_id']),
                'campaign_name':   r['campaign_name'],
                'total_calls':     r['total_calls'],
                'converted_calls': r['converted_calls'],
                'conversion_rate': round((r['converted_calls'] / total) * 100, 2),
                'total_revenue':   r['total_revenue'],
                'total_payout':    r['total_payout'],
                'total_profit':    r['total_profit'],
                'avg_duration':    round(r['avg_duration'] or 0, 1),
                'spam_blocked':    r['spam_blocked'],
            })
        return result

    # ── buyer performance ────────────────────────────────────────────────────

    @staticmethod
    def get_buyer_performance(user: User, filters) -> list:
        qs = AnalyticsService._base_qs(user, filters)

        rows = (
            qs
            .exclude(buyer_id=None)
            .values('buyer_id', 'buyer_name')
            .annotate(
                total_calls=Count('id'),
                converted=Count('id', filter=Q(is_converted=True)),
                total_payout=Coalesce(Sum('payout'), Decimal('0')),
                avg_bid=Coalesce(Avg('winning_bid'), Decimal('0')),
                avg_duration=Coalesce(Avg('duration_seconds'), 0.0),
            )
            .order_by('-total_calls')
        )

        result = []
        for r in rows:
            total = r['total_calls'] or 1
            result.append({
                'buyer_id':       str(r['buyer_id']),
                'buyer_name':     r['buyer_name'],
                'total_calls':    r['total_calls'],
                'won_calls':      r['converted'],
                'avg_bid':        r['avg_bid'],
                'total_payout':   r['total_payout'],
                'avg_duration':   round(r['avg_duration'] or 0, 1),
                'conversion_rate': round((r['converted'] / total) * 100, 2),
            })
        return result

    # ── publisher performance ────────────────────────────────────────────────

    @staticmethod
    def get_publisher_performance(user: User, filters) -> list:
        qs = AnalyticsService._base_qs(user, filters)

        rows = (
            qs
            .exclude(publisher_id=None)
            .values('publisher_id', 'publisher_name')
            .annotate(
                total_calls=Count('id'),
                converted=Count('id', filter=Q(is_converted=True)),
                total_revenue=Coalesce(Sum('revenue'), Decimal('0')),
                spam_count=Count('id', filter=Q(is_spam=True)),
            )
            .order_by('-total_calls')
        )

        result = []
        for r in rows:
            total = r['total_calls'] or 1
            result.append({
                'publisher_id':    str(r['publisher_id']),
                'publisher_name':  r['publisher_name'],
                'total_calls':     r['total_calls'],
                'converted_calls': r['converted'],
                'conversion_rate': round((r['converted'] / total) * 100, 2),
                'total_revenue':   r['total_revenue'],
                'spam_rate':       round((r['spam_count'] / total) * 100, 2),
            })
        return result

    # ── call log ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_call_log(user: User, filters) -> dict:
        qs = AnalyticsService._base_qs(user, filters).order_by('-created_at')
        total = qs.count()
        items = qs[filters.offset: filters.offset + filters.limit]

        return {
            'total':  total,
            'offset': filters.offset,
            'limit':  filters.limit,
            'items':  [AnalyticsService._format_record(r) for r in items],
        }

    @staticmethod
    def _format_record(r: CallRecord) -> dict:
        return {
            'id':               str(r.id),
            'twilio_call_sid':  r.twilio_call_sid,
            'caller_number':    r.caller_number,
            'caller_state':     r.caller_state,
            'called_number':    r.called_number,
            'campaign_id':      str(r.campaign_id) if r.campaign_id else None,
            'campaign_name':    r.campaign_name,
            'buyer_id':         str(r.buyer_id) if r.buyer_id else None,
            'buyer_name':       r.buyer_name,
            'publisher_id':     str(r.publisher_id) if r.publisher_id else None,
            'publisher_name':   r.publisher_name,
            'status':           r.status,
            'duration_seconds': r.duration_seconds,
            'is_converted':     r.is_converted,
            'is_duplicate':     r.is_duplicate,
            'is_spam':          r.is_spam,
            'revenue':          r.revenue,
            'payout':           r.payout,
            'profit':           r.profit,
            'winning_bid':      r.winning_bid,
            'recording_url':    r.recording_url,
            'started_at':       r.started_at,
            'ended_at':         r.ended_at,
            'created_at':       r.created_at,
        }

    # ── CSV export ───────────────────────────────────────────────────────────

    @staticmethod
    def export_csv(user: User, filters) -> str:
        qs = AnalyticsService._base_qs(user, filters).order_by('-created_at')

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'Date', 'Caller', 'State', 'Called Number',
            'Campaign', 'Buyer', 'Publisher',
            'Status', 'Duration (s)', 'Converted',
            'Revenue', 'Payout', 'Profit', 'Recording'
        ])

        for r in qs:
            writer.writerow([
                r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                r.caller_number, r.caller_state, r.called_number,
                r.campaign_name, r.buyer_name, r.publisher_name,
                r.status, r.duration_seconds, r.is_converted,
                r.revenue, r.payout, r.profit, r.recording_url,
            ])

        return output.getvalue()

    # ── record call (called by twilio webhook after call ends) ────────────────

    @staticmethod
    def record_call(data: dict, organization) -> CallRecord:
        """
        Called from twilio/webhook after call-status update.
        data keys match Twilio's StatusCallback params.
        """
        record, _ = CallRecord.objects.update_or_create(
            twilio_call_sid=data.get('CallSid', ''),
            organization=organization,
            defaults={
                'caller_number':      data.get('From', ''),
                'called_number':      data.get('To', ''),
                'status':             _map_twilio_status(data.get('CallStatus', '')),
                'duration_seconds':   int(data.get('CallDuration', 0) or 0),
                'campaign_id':        data.get('campaign_id'),
                'campaign_name':      data.get('campaign_name', ''),
                'buyer_id':           data.get('buyer_id'),
                'buyer_name':         data.get('buyer_name', ''),
                'publisher_id':       data.get('publisher_id'),
                'publisher_name':     data.get('publisher_name', ''),
                'revenue':            Decimal(str(data.get('revenue', '0'))),
                'payout':             Decimal(str(data.get('payout', '0'))),
                'profit':             Decimal(str(data.get('profit', '0'))),
                'winning_bid':        Decimal(str(data.get('winning_bid', '0'))) if data.get('winning_bid') else None,
                'is_converted':       bool(data.get('is_converted', False)),
                'is_duplicate':       bool(data.get('is_duplicate', False)),
                'is_spam':            bool(data.get('is_spam', False)),
                'recording_url':      data.get('RecordingUrl', ''),
                'caller_state':       data.get('caller_state', ''),
                'routing_type':       data.get('routing_type', ''),
                'auction_id':         data.get('auction_id'),
            }
        )
        # auto-compute profit
        if record.revenue and record.payout:
            record.profit = record.revenue - record.payout
            record.save(update_fields=['profit'])

        return record


def _map_twilio_status(twilio_status: str) -> str:
    mapping = {
        'completed':  'completed',
        'no-answer':  'no_answer',
        'busy':       'busy',
        'failed':     'failed',
        'in-progress': 'in_progress',
        'canceled':   'failed',
    }
    return mapping.get(twilio_status, 'completed')



class CallerProfileService:
    """Aggregated view of a caller across all calls."""

    @staticmethod
    def get_profile(organization_id: str, caller_number: str) -> dict:
        from django.db.models import Count, Sum, Avg, Max, Min
        from routing.models import CallLog

        calls = CallLog.objects.filter(
            organization_id=organization_id,
            caller_number=caller_number,
        )

        total = calls.count()
        if total == 0:
            return {
                'caller_number': caller_number,
                'total_calls': 0,
                'profile_exists': False,
            }

        stats = calls.aggregate(
            total_duration=Sum('duration'),
            avg_duration=Avg('duration'),
            total_revenue=Sum('revenue'),
            first_call=Min('created_at'),
            last_call=Max('created_at'),
        )

        completed = calls.filter(status=CallLog.Status.COMPLETED).count()
        unique_campaigns = calls.values('campaign_id').distinct().count()
        unique_buyers = calls.exclude(buyer__isnull=True).values('buyer_id').distinct().count()

        caller_state = calls.exclude(caller_state='').values_list('caller_state', flat=True).first() or ''

        return {
            'caller_number': caller_number,
            'caller_state': caller_state,
            'profile_exists': True,
            'total_calls': total,
            'completed_calls': completed,
            'completion_rate': round((completed / total) * 100, 2) if total else 0,
            'total_duration_seconds': stats['total_duration'] or 0,
            'avg_duration_seconds': round(stats['avg_duration'] or 0, 2),
            'total_revenue': str(stats['total_revenue'] or 0),
            'unique_campaigns': unique_campaigns,
            'unique_buyers_reached': unique_buyers,
            'first_call_at': stats['first_call'].isoformat() if stats['first_call'] else None,
            'last_call_at': stats['last_call'].isoformat() if stats['last_call'] else None,
        }