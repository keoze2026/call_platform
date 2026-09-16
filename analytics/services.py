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
from routing.models import CallLog
from accounts.models import User


def _map_twilio_status(status_str):
    mapping = {'completed': 'completed', 'busy': 'busy', 'no-answer': 'no_answer', 'failed': 'failed', 'canceled': 'cancelled'}
    return mapping.get((status_str or '').lower(), 'completed')


class AnalyticsService:

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _base_qs(user: User, filters):
        qs = CallRecord.objects.filter(organization=user.organization)

        val_from = filters.date_from or filters.start_date or filters.created_at__gte
        if val_from:
            dt = parse_datetime(val_from + 'T00:00:00') or datetime.fromisoformat(val_from)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            qs = qs.filter(created_at__gte=dt)

        val_to = filters.date_to or filters.end_date or filters.created_at__lte
        if val_to:
            dt = parse_datetime(val_to + 'T23:59:59') or datetime.fromisoformat(val_to)
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
    def get_dashboard(user: User, filters=None) -> dict:
        org = user.organization
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if filters:
            all_qs = AnalyticsService._base_qs(user, filters)
        else:
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

        if filters and any([filters.date_from, filters.date_to, filters.created_at__gte, filters.created_at__lte, getattr(filters, 'start_date', None), getattr(filters, 'end_date', None)]):
            calls_today = agg['total_calls'] or 0
        else:
            calls_today = all_qs.filter(created_at__gte=today_start).count()
        live_calls  = CallLog.objects.filter(campaign__organization=org, status__in=['in_progress', 'ringing', 'initiated']).count()

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
                total_calls=Count('id', filter=~Q(status__in=['failed', 'no_answer', 'busy', 'canceled'])),
                qualified_calls=Count('id', filter=Q(is_qualified=True)),
                converted_calls=Count('id', filter=Q(is_converted=True)),
                total_revenue=Coalesce(Sum('revenue'), Decimal('0')),
                total_payout=Coalesce(Sum('payout'), Decimal('0')),
                total_profit=Coalesce(Sum('profit'), Decimal('0')),
                avg_duration=Coalesce(Avg('duration_seconds', filter=~Q(status__in=['failed', 'no_answer', 'busy', 'canceled'])), 0.0),
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
                'qualified_calls': r['qualified_calls'],
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
                total_calls=Count('id', filter=~Q(status__in=['failed', 'no_answer', 'busy', 'canceled'])),
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
                total_calls=Count('id', filter=~Q(status__in=['failed', 'no_answer', 'busy', 'canceled'])),
                qualified_calls=Count('id', filter=Q(is_qualified=True)),
                converted=Count('id', filter=Q(is_converted=True)),
                total_revenue=Coalesce(Sum('revenue'), Decimal('0')),
                spam_count=Count('id', filter=Q(is_spam=True)),
                avg_duration=Coalesce(Avg('duration_seconds', filter=~Q(status__in=['failed', 'no_answer', 'busy', 'canceled'])), 0.0),
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
                'qualified_calls': r['qualified_calls'],
                'converted_calls': r['converted'],
                'conversion_rate': round((r['converted'] / total) * 100, 2),
                'total_revenue':   r['total_revenue'],
                'spam_rate':       round((r['spam_count'] / total) * 100, 2),
                'avg_duration':    round(r['avg_duration'] or 0, 1),
            })
        return result

    # ── call log ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_call_log(user: User, filters) -> dict:
        from routing.models import CallLog
        qs = AnalyticsService._base_qs(user, filters).order_by('-created_at')
        total = qs.count()
        items = list(qs[filters.offset: filters.offset + filters.limit])

        sids = [r.twilio_call_sid for r in items if r.twilio_call_sid]
        dest_map = dict(
            CallLog.objects.filter(twilio_call_sid__in=sids)
            .values_list('twilio_call_sid', 'destination_number')
        )

        return {
            'total':  total,
            'offset': filters.offset,
            'limit':  filters.limit,
            'items':  [AnalyticsService._format_record(r, dest_map.get(r.twilio_call_sid)) for r in items],
        }

    @staticmethod
    def _format_record(r: CallRecord, destination_number: str = None) -> dict:
        dest_num = destination_number or ''
        dt_start = r.started_at or r.created_at
        status_val = 'in-progress' if r.status in ['in_progress', 'in-progress'] else ('failed' if r.status in ['failed', 'no_answer', 'busy', 'canceled'] else r.status)
        return {
            'id':               str(r.id),
            'twilio_call_sid':  r.twilio_call_sid,
            'caller_number':    (lambda rc: (d:=''.join(filter(str.isdigit, rc or ''))) and (d[1:] if d.startswith('1') and len(d)==11 else d))(r.caller_number),
            'caller_state':     r.caller_state,
            'called_number':    r.called_number,
            'destination_number': dest_num,
            'destinationNumber':  dest_num,
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
            'started_at':       dt_start,
            'startedAt':        int(dt_start.timestamp() * 1000) if dt_start else None,
            'ended_at':         r.ended_at,
            'created_at':       r.created_at,
            'ipqs_line_type':   r.ipqs_line_type,
        }

    # ── CSV export ───────────────────────────────────────────────────────────

    @staticmethod
    def export_csv(user: User, filters):
        class PseudoBuffer:
            def write(self, value):
                return value
                
        qs = AnalyticsService._base_qs(user, filters).order_by('-created_at')
        writer = csv.writer(PseudoBuffer())
        
        yield writer.writerow([
            'Date', 'Caller', 'State', 'Called Number',
            'Campaign', 'Buyer', 'Publisher',
            'Status', 'Duration (s)', 'Converted',
            'Revenue', 'Payout', 'Profit', 'Recording'
        ])

        for r in qs.iterator(chunk_size=2000):
            raw_caller = r.caller_number or ''
            clean_caller = raw_caller.lstrip('+')
            if clean_caller.startswith('1') and len(clean_caller) == 11:
                clean_caller = clean_caller[1:]
            yield writer.writerow([
                r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                clean_caller, r.caller_state, r.called_number,
                r.campaign_name, r.buyer_name, r.publisher_name,
                r.status, r.duration_seconds, r.is_converted,
                r.revenue, r.payout, r.profit, r.recording_url,
            ])


    @staticmethod
    def record_call(data: dict, organization) -> CallRecord:
        """
        Called from twilio/webhook after call-status update.
        data keys match Twilio's StatusCallback params.
        """
        raw_from = data.get('From', '')
        digits_only = ''.join(filter(str.isdigit, raw_from))
        if digits_only.startswith('1') and len(digits_only) == 11:
            clean_from = digits_only[1:]
        else:
            clean_from = digits_only or raw_from.lstrip('+')

                # Resolve payout with fallback to buyer configuration if missing or zero
        _p_val = data.get('payout') or data.get('buyer_payout')
        if not _p_val or float(_p_val) == 0:
            from buyers.models import Buyer
            _b_id = data.get('buyer_id')
            if _b_id:
                _buyer_obj = Buyer.objects.filter(id=_b_id).first()
                if _buyer_obj and _buyer_obj.payout_amount:
                    _p_val = _buyer_obj.payout_amount
        final_payout = Decimal(str(_p_val or '0'))

        record, _ = CallRecord.objects.update_or_create(
            twilio_call_sid=data.get('CallSid', ''),
            organization=organization,
            defaults={
                'caller_number': clean_from,
                'called_number': data.get('To', ''),
                'status': _map_twilio_status(data.get('CallStatus', '')),
                'duration_seconds': int(data.get('CallDuration', 0) or 0),
                'campaign_id': data.get('campaign_id'),
                'campaign_name': data.get('campaign_name', ''),
                'buyer_id': data.get('buyer_id'),
                'buyer_name': data.get('buyer_name', ''),
            'publisher_id': data.get('publisher_id') or data.get('pub_id') or data.get('publisher') or data.get('affiliate_id'),
            'publisher_name': data.get('publisher_name') or data.get('pub_name') or data.get('publisher_title') or data.get('affiliate_name') or '',
                'revenue': Decimal(str(data.get('revenue', '0'))),
                'payout': final_payout,
                'profit': Decimal(str(data.get('profit', '0'))),
                'winning_bid': Decimal(str(data.get('winning_bid', '0'))) if data.get('winning_bid') else None,
                'is_converted': bool(data.get('is_converted', False)),
                'is_qualified': bool(data.get('is_qualified', False)),
                'is_duplicate': bool(data.get('is_duplicate', False)),
                'is_spam': bool(data.get('is_spam', False)),
                'recording_url': data.get('RecordingUrl', '') or data.get('recording_url', '') or data.get('Recording', '') or data.get('media_url', '') or data.get('audio_url', '') or data.get('recording_link', '') or data.get('file_url', '') or data.get('url', ''),
                'caller_state': data.get('caller_state', ''),
                'routing_type': data.get('routing_type', ''),
                'auction_id': data.get('auction_id'),
            }
        )
        if record.revenue and record.payout:
            record.profit = record.revenue - record.payout
            record.save(update_fields=['profit'])

        return record


    @staticmethod
    def get_profile(organization_id: str, caller_number: str) -> dict:
        from django.db.models import Count, Sum, Avg, Max, Min
        from routing.models import CallLog

        calls = CallLog.objects.filter(
            organization_id=organization_id,
            caller_number=caller_number,
        )

        total = calls.count()
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