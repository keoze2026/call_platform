from django.db.models import (
    Count, Sum, Avg, Q, F, Case, When, Value,
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
from routing.recordings import public_recording_url
from accounts.models import User
from accounts.permissions import scope_queryset


def _map_twilio_status(status_str):
    mapping = {'completed': 'completed', 'busy': 'busy', 'no-answer': 'no_answer', 'failed': 'failed', 'canceled': 'cancelled'}
    return mapping.get((status_str or '').lower(), 'completed')


def _account_balance(organization) -> Decimal:
    """Organization credit, or zero when billing is unavailable.

    Never raises: a billing problem must not take the dashboard down with it.
    """
    try:
        from billing.services import BillingService
        return BillingService.get_balance(organization)
    except Exception:
        return Decimal('0')


def _account_currency(organization) -> str:
    try:
        from billing.models import BillingAccount
        account = BillingAccount.objects.only('currency').get(organization=organization)
        return account.currency or 'USD'
    except Exception:
        return 'USD'


class AnalyticsService:

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _base_qs(user: User, filters):
        from django.db.models import Case, When, F, DecimalField
        from django.db.models.functions import Coalesce
        from decimal import Decimal
        
        qs = CallRecord.objects.filter(organization=user.organization)

        val_from = filters.date_from or getattr(filters, 'start_date', None) or getattr(filters, 'created_at__gte', None)
        if val_from:
            dt = parse_datetime(val_from + 'T00:00:00') or datetime.fromisoformat(val_from)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            qs = qs.filter(created_at__gte=dt)

        val_to = filters.date_to or getattr(filters, 'end_date', None) or getattr(filters, 'created_at__lte', None)
        if val_to:
            dt = parse_datetime(val_to + 'T23:59:59') or datetime.fromisoformat(val_to)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            qs = qs.filter(created_at__lte=dt)

        if getattr(filters, 'campaign_id', None):
            qs = qs.filter(campaign_id=filters.campaign_id)

        if getattr(filters, 'buyer_id', None):
            qs = qs.filter(buyer_id=filters.buyer_id)

        if getattr(filters, 'publisher_id', None):
            qs = qs.filter(publisher_id=filters.publisher_id)

        if getattr(filters, 'status', None):
            qs = qs.filter(status=filters.status)

        for flag in ('is_qualified', 'is_converted', 'is_duplicate', 'is_spam'):
            value = getattr(filters, flag, None)
            if value is not None:
                qs = qs.filter(**{flag: value})

        # A buyer or publisher login sees only its own calls. Applied here
        # rather than per endpoint so every report, chart, drill-down and export
        # built on this queryset is narrowed the same way - one place to be
        # right, and nothing new can leak by forgetting to filter.
        qs = scope_queryset(user, qs)
            
        # Revenue and payout are earned per *converted* call, so an unconverted
        # call contributes zero. Applying campaign pricing to every row credited
        # calls that never connected — 83 incoming were billed as 83 conversions.
        # A call earns when it converts: answered AND at least the campaign's
        # min_call_duration. Billing already uses that rule, so gating reporting
        # on status alone counted short answered calls as revenue that was never
        # charged. is_converted is safe to use now that the signal populates it
        # and backfill_converted has fixed the historical rows.
        zero = Value(Decimal('0'), output_field=DecimalField(max_digits=10, decimal_places=4))
        earned = Q(is_converted=True)

        qs = qs.annotate(
            dynamic_revenue=Case(
                When(~earned, then=zero),
                When(campaign__isnull=False, then=F('campaign__revenue_amount')),
                default=F('revenue'),
                output_field=DecimalField(max_digits=10, decimal_places=4)
            ),
            dynamic_payout=Case(
                When(~earned, then=zero),
                When(campaign__isnull=False, then=F('campaign__payout_amount')),
                default=F('payout'),
                output_field=DecimalField(max_digits=10, decimal_places=4)
            )
        ).annotate(
            dynamic_profit=F('dynamic_revenue') - F('dynamic_payout')
        )

        return qs

    @staticmethod
    def _live_qs(user: User, filters):
        qs = CallLog.objects.filter(
            organization=user.organization,
            status__in=['in_progress', 'ringing', 'initiated']
        )
        val_from = filters.date_from or getattr(filters, 'start_date', None) or getattr(filters, 'created_at__gte', None)
        if val_from:
            dt = parse_datetime(val_from + 'T00:00:00') or datetime.fromisoformat(val_from)
            if timezone.is_naive(dt): dt = timezone.make_aware(dt)
            qs = qs.filter(created_at__gte=dt)

        val_to = filters.date_to or getattr(filters, 'end_date', None) or getattr(filters, 'created_at__lte', None)
        if val_to:
            dt = parse_datetime(val_to + 'T23:59:59') or datetime.fromisoformat(val_to)
            if timezone.is_naive(dt): dt = timezone.make_aware(dt)
            qs = qs.filter(created_at__lte=dt)

        if getattr(filters, 'campaign_id', None): qs = qs.filter(campaign_id=filters.campaign_id)
        if getattr(filters, 'buyer_id', None): qs = qs.filter(buyer_id=filters.buyer_id)
        if getattr(filters, 'publisher_id', None): qs = qs.filter(publisher_id=filters.publisher_id)
        if getattr(filters, 'status', None) and filters.status not in ['in_progress', 'ringing', 'initiated']:
            qs = qs.none()
        return scope_queryset(user, qs)

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
            all_qs = AnalyticsService._base_qs(user, filters=type('Obj', (object,), {})())

        agg = all_qs.aggregate(
            total_calls=Count('id'),
            completed=Count('id', filter=Q(status='completed')),
            converted=Count('id', filter=Q(is_converted=True)),
            spam=Count('id', filter=Q(is_spam=True)),
            connected=Count('id', filter=Q(status__in=['completed', 'in_progress'])),
            repeat_answered=Count('id', filter=Q(
                status__in=['completed', 'in_progress'], is_duplicate=True,
            )),
            total_revenue=Coalesce(Sum('dynamic_revenue'), Decimal('0')),
            total_payout=Coalesce(Sum('dynamic_payout'), Decimal('0')),
            total_profit=Coalesce(Sum('dynamic_profit'), Decimal('0')),
            avg_duration=Coalesce(Avg('duration_seconds'), 0.0),
        )

        if filters and any([getattr(filters, 'date_from', None), getattr(filters, 'date_to', None), getattr(filters, 'created_at__gte', None), getattr(filters, 'created_at__lte', None), getattr(filters, 'start_date', None), getattr(filters, 'end_date', None)]):
            calls_today = agg['total_calls'] or 0
        else:
            calls_today = all_qs.filter(created_at__gte=today_start).count()
            
        live_calls = AnalyticsService._live_qs(user, filters).count()
        actual_total_calls = (agg['total_calls'] or 0) + live_calls

        total = actual_total_calls or 1
        return {
            'total_calls':       actual_total_calls,
            'calls_today':       calls_today + live_calls,
            'live_calls':        live_calls,
            'completed_calls':   agg['completed'],
            'converted_calls':   agg['converted'],
            'conversion_rate':   round((agg['converted'] / total) * 100, 2),
            'total_revenue':     agg['total_revenue'],
            'total_payout':      agg['total_payout'],
            'total_profit':      agg['total_profit'],
            'avg_call_duration': round(agg['avg_duration'] or 0, 1),
            'balance':           _account_balance(org),
            'currency':          _account_currency(org),
            'spam_blocked':      agg['spam'],
            # Answered calls from a caller who rang before, matching the summary table
            'duplicate_blocked': agg['repeat_answered'] or 0,
        }

    # ── time series ──────────────────────────────────────────────────────────

    @staticmethod
    def get_time_series(user: User, filters) -> list:
        qs = AnalyticsService._base_qs(user, filters)
        live_qs = AnalyticsService._live_qs(user, filters)

        trunc_map = {
            'hour':  TruncHour,
            'day':   TruncDay,
            'week':  TruncWeek,
            'month': TruncMonth,
        }
        trunc_fn = trunc_map.get(getattr(filters, 'granularity', 'day') or 'day', TruncDay)

        live_rows = dict(
            live_qs.annotate(period=trunc_fn('created_at'))
            .values_list('period')
            .annotate(calls=Count('id'))
        )

        rows = (
            qs
            .annotate(period=trunc_fn('created_at'))
            .values('period')
            .annotate(
                calls=Count('id'),
                converted=Count('id', filter=Q(is_converted=True)),
                revenue=Coalesce(Sum('dynamic_revenue'), Decimal('0')),
                payout=Coalesce(Sum('dynamic_payout'), Decimal('0')),
                profit=Coalesce(Sum('dynamic_profit'), Decimal('0')),
                avg_duration=Coalesce(Avg('duration_seconds'), 0.0),
            )
            .order_by('period')
        )

        result = []
        # Merge live calls into historical, tracking which periods we've seen
        seen_periods = set()
        for r in rows:
            period = r['period']
            seen_periods.add(period)
            live_c = live_rows.get(period, 0)
            result.append({
                'period':       period.isoformat() if period else '',
                'calls':        r['calls'] + live_c,
                'converted':    r['converted'],
                'revenue':      r['revenue'],
                'payout':       r['payout'],
                'profit':       r['profit'],
                'avg_duration': round(r['avg_duration'] or 0, 1),
            })
            
        # Add periods that only exist in live_calls
        for period, live_c in live_rows.items():
            if period not in seen_periods:
                result.append({
                    'period':       period.isoformat() if period else '',
                    'calls':        live_c,
                    'converted':    0,
                    'revenue':      Decimal('0'),
                    'payout':       Decimal('0'),
                    'profit':       Decimal('0'),
                    'avg_duration': 0.0,
                })
                
        result.sort(key=lambda x: x['period'])
        return result

    # ── campaign performance ─────────────────────────────────────────────────

    @staticmethod
    def get_campaign_performance(user: User, filters) -> list:
        qs = AnalyticsService._base_qs(user, filters)
        live_qs = AnalyticsService._live_qs(user, filters)
        live_counts = dict(live_qs.exclude(campaign_id=None).values_list('campaign_id').annotate(c=Count('id')))

        rows = (
            qs
            .exclude(campaign_id=None)
            .values('campaign_id', 'campaign_name')
            .annotate(
                total_calls=Count('id'),
                qualified_calls=Count('id', filter=Q(status__in=[CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS]) & ~Q(is_duplicate=True)),
                converted_calls=Count('id', filter=Q(is_converted=True)),
                total_revenue=Coalesce(Sum('dynamic_revenue'), Decimal('0')),
                total_payout=Coalesce(Sum('dynamic_payout'), Decimal('0')),
                total_profit=Coalesce(Sum('dynamic_profit'), Decimal('0')),
                avg_duration=Coalesce(Avg('duration_seconds', filter=~Q(status__in=['failed', 'no_answer', 'busy', 'canceled'])), 0.0),
                spam_blocked=Count('id', filter=Q(is_spam=True)),
                
                # Columns the summary table was deriving client-side. Definitions
                # agreed with the frontend: connected and not-connected are
                # complements, so the two always sum to total_calls.
                repeat_answered=Count('id', filter=Q(status__in=[CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS]) & Q(is_duplicate=True)),
                connected_calls=Count('id', filter=Q(status__in=[
                    CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS,
                ])),
                not_connected_calls=Count('id', filter=~Q(status__in=[
                    CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS,
                ])),
                paid_calls=Count('id', filter=Q(
                    is_converted=True, campaign__payout_amount__gt=0,
                )),
                total_duration_sec=Coalesce(Sum('duration_seconds'), 0),
            )
        )

        result = []
        for r in rows:
            cid = r['campaign_id']
            lc = live_counts.pop(cid, 0)
            total = r['total_calls'] + lc
            t_total = total or 1
            result.append({
                'campaign_id':     str(cid),
                'campaign_name':   r['campaign_name'],
                'total_calls':     total,
                'qualified_calls': r['qualified_calls'],
                'converted_calls': r['converted_calls'],
                'conversion_rate': round((r['converted_calls'] / t_total) * 100, 2),
                'total_revenue':   r['total_revenue'],
                'total_payout':    r['total_payout'],
                'total_profit':    r['total_profit'],
                'avg_duration':    round(r['avg_duration'] or 0, 1),
                'spam_blocked':    r['spam_blocked'],
                'duplicate_calls': r['repeat_answered'],
                # Aliases: the summary table's DUPE column has been seen reading
                # each of these spellings.
                'dupe': r['repeat_answered'],
                'duplicates': r['repeat_answered'],
                'connected_calls': r['connected_calls'],
                'not_connected_calls': r['not_connected_calls'],
                'paid_calls':      r['paid_calls'],
                'live_calls':      lc,
                'total_duration_sec': r['total_duration_sec'],
            })

        if live_counts:
            from campaigns.models import Campaign
            camps = {c.id: c.name for c in Campaign.objects.filter(id__in=live_counts.keys())}
            for cid, lc in live_counts.items():
                result.append({
                    'campaign_id': str(cid),
                    'campaign_name': camps.get(cid, 'Unknown'),
                    'total_calls': lc,
                    'qualified_calls': 0,
                    'converted_calls': 0,
                    'conversion_rate': 0.0,
                    'total_revenue': Decimal('0'),
                    'total_payout': Decimal('0'),
                    'total_profit': Decimal('0'),
                    'avg_duration': 0.0,
                    'spam_blocked': 0,
                    'duplicate_calls': 0,
                    'dupe': 0,
                    'duplicates': 0,
                    'connected_calls': lc,
                    'not_connected_calls': 0,
                    'paid_calls': 0,
                    'live_calls': lc,
                    'total_duration_sec': 0,
                })
                
        result.sort(key=lambda x: x['total_calls'], reverse=True)
        return result

    # ── buyer performance ────────────────────────────────────────────────────

    @staticmethod
    def format_call_detail(call) -> dict:
        """Assemble the full picture of one call.

        caller_profile   who rang, from the Telnyx lookup plus what the carrier
                         sent. Fields Telnyx does not return are null rather than
                         zero, so the client can hide them instead of showing a
                         fraud score of 0 that means 'unknown'.
        routing          which rule ran and where the call went.
        timeline         ordered events with timestamps, each one derived from a
                         stored value - nothing is inferred or invented.
        financials       resolved the same way reporting resolves them.
        """
        campaign = call.campaign

        def iso(dt):
            return dt.isoformat() if dt else None

        # Telnyx returns carrier and portability only. A fraud score of 0 from it
        # means "not provided", so it is reported as null unless a lookup ran.
        profile = {
            'caller_number': call.caller_number,
            'local_format': AnalyticsService._local_format(call.caller_number),
            'area_code': call.caller_area_code or None,
            'region': call.caller_state or None,
            'country': call.caller_country or None,
            'carrier': call.carrier or None,
            'carrier_raw': call.carrier_name or None,
            'line_type': call.ipqs_line_type or None,
            'is_voip': call.ipqs_is_voip,
            # The current provider does not supply this - its wrapper returns 0
            # meaning 'not provided', which reads as a clean score on screen.
            'fraud_score': (
                call.ipqs_fraud_score
                if call.ipqs_checked and call.ipqs_fraud_score else None
            ),
            'lookup_performed': call.ipqs_checked,
            # Not available from the current lookup provider
            'city': None,
            'zip_code': None,
            'timezone': None,
        }

        timeline = [{
            'event': 'call_received',
            'label': 'Call Received',
            'at': iso(call.created_at),
            'detail': {'from': call.caller_number, 'to': call.called_number},
        }]

        if call.ipqs_checked:
            timeline.append({
                'event': 'caller_lookup',
                'label': 'Caller Profile',
                'at': iso(call.created_at),
                'detail': {'carrier': call.carrier or None, 'line_type': call.ipqs_line_type or None},
            })

        if call.destination_number:
            timeline.append({
                'event': 'destination_dialed',
                'label': 'Destination Dialed',
                'at': iso(call.created_at),
                'detail': {
                    'destination': call.destination_number,
                    'buyer': call.buyer.name if call.buyer_id else None,
                },
            })

        if call.answered_at:
            timeline.append({
                'event': 'connected',
                'label': 'Connected Call',
                'at': iso(call.answered_at),
                'detail': {'destination': call.destination_number or None},
            })

        min_dur = getattr(campaign, 'min_call_duration', 0) if campaign else 0
        converted = (
            call.status == call.Status.COMPLETED and (call.duration or 0) >= (min_dur or 0)
        )

        if converted:
            timeline.append({
                'event': 'converted',
                'label': 'Converted Call',
                'at': iso(call.ended_at),
                'detail': {
                    'buyer': call.buyer.name if call.buyer_id else None,
                    'destination': call.destination_number or None,
                    'conversion_amount': str(call.revenue or 0),
                },
            })

        if call.ended_at:
            timeline.append({
                'event': 'ended',
                'label': 'Call Ended',
                'at': iso(call.ended_at),
                'detail': {
                    'status': call.status,
                    'duration_seconds': call.duration or 0,
                    'reason': call.block_reason or call.ipqs_block_reason or None,
                },
            })

        # answered_at is derived as (hangup - duration) and can land a fraction
        # before created_at, which renders the timeline out of order.
        started = call.created_at
        for event in timeline:
            if event['at'] and started and event['at'] < started.isoformat():
                event['at'] = started.isoformat()
        timeline.sort(key=lambda e: e['at'] or '')

        return {
            'id': str(call.id),
            'call_sid': call.twilio_call_sid,
            'status': call.status,
            'duration_seconds': call.duration or 0,
            'is_duplicate': call.is_duplicate,
            'is_converted': converted,
            'created_at': iso(call.created_at),

            'caller_profile': profile,

            'routing': {
                'called_number': call.called_number,
                'campaign_id': str(call.campaign_id) if call.campaign_id else None,
                'campaign_name': campaign.name if call.campaign_id else None,
                'rule_id': str(call.routing_rule_id) if call.routing_rule_id else None,
                'rule_name': call.routing_rule.name if call.routing_rule_id else None,
                'rule_type': call.routing_rule.rule_type if call.routing_rule_id else None,
                'destination_number': call.destination_number or None,
                'buyer_id': str(call.buyer_id) if call.buyer_id else None,
                'buyer_name': call.buyer.name if call.buyer_id else None,
                'publisher_name': call.publisher.name if call.publisher_id else None,
                'block_reason': call.block_reason or call.ipqs_block_reason or None,
            },

            'financials': {
                'revenue': str((campaign.revenue_amount if campaign and converted else 0) or 0),
                'payout': str(call.publisher_payout or 0),
                'profit': str(
                    ((campaign.revenue_amount if campaign and converted else 0) or 0)
                    - (call.publisher_payout or 0)
                ),
                'min_call_duration': min_dur or 0,
            },

            # How the destination was chosen. Empty on calls placed before this
            # was recorded, so the client should treat {} as "not available"
            # rather than "nothing was considered".
            'routing_trace': call.routing_trace or {},

            'recording': {
                'url': public_recording_url(call.recording_url) or None,
                'transcription': call.transcription_text or None,
                'sentiment': call.sentiment or None,
            },

            'timeline': timeline,
        }

    @staticmethod
    def _local_format(number: str) -> str:
        """(410) 392-5785 from +14103925785. Returns the input if it is not NANP."""
        digits = ''.join(c for c in (number or '') if c.isdigit())
        if len(digits) == 11 and digits.startswith('1'):
            digits = digits[1:]
        if len(digits) != 10:
            return number or ''
        return f'({digits[:3]}) {digits[3:6]}-{digits[6:]}'

    @staticmethod
    def get_carrier_performance(user: User, filters) -> list:
        """Breakdown by caller carrier, for the CALLER PROFILE tab.

        Groups on the normalised carrier family, so 'Verizon Wireless:6006' and
        'CELLCO PARTNERSHIP DBA VERIZON WIRELESS - OH' land in one row instead of
        two. Calls whose lookup has not run or returned nothing are grouped as
        'Unknown' rather than dropped, so the rows still sum to the total.
        """
        qs = AnalyticsService._base_qs(user, filters)

        rows = (
            qs
            .values('carrier')
            .annotate(
                total_calls=Count('id'),
                qualified_calls=Count('id', filter=Q(status__in=[CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS]) & ~Q(is_duplicate=True)),
                converted_calls=Count('id', filter=Q(is_converted=True)),
                total_revenue=Coalesce(Sum('dynamic_revenue'), Decimal('0')),
                total_payout=Coalesce(Sum('dynamic_payout'), Decimal('0')),
                total_profit=Coalesce(Sum('dynamic_profit'), Decimal('0')),
                avg_duration=Coalesce(Avg('duration_seconds', filter=~Q(
                    status__in=['failed', 'no_answer', 'busy', 'canceled']
                )), 0.0),
                spam_blocked=Count('id', filter=Q(is_spam=True)),
                
                repeat_answered=Count('id', filter=Q(status__in=[CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS]) & Q(is_duplicate=True)),
                connected_calls=Count('id', filter=Q(status__in=[
                    CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS,
                ])),
                not_connected_calls=Count('id', filter=~Q(status__in=[
                    CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS,
                ])),
                paid_calls=Count('id', filter=Q(
                    is_converted=True, campaign__payout_amount__gt=0,
                )),
                total_duration_sec=Coalesce(Sum('duration_seconds'), 0),
            )
            .order_by('-total_calls')
        )

        result = []
        for r in rows:
            total = r['total_calls'] or 1
            result.append({
                'carrier':         r['carrier'] or 'Unknown',
                'carrier_name':    r['carrier'] or 'Unknown',
                'total_calls':     r['total_calls'],
                'qualified_calls': r['qualified_calls'],
                'converted_calls': r['converted_calls'],
                'conversion_rate': round((r['converted_calls'] / total) * 100, 2),
                'total_revenue':   r['total_revenue'],
                'total_payout':    r['total_payout'],
                'total_profit':    r['total_profit'],
                'avg_duration':    round(r['avg_duration'] or 0, 1),
                'spam_blocked':    r['spam_blocked'],
                'duplicate_calls': r['repeat_answered'],
                'dupe': r['repeat_answered'],
                'duplicates': r['repeat_answered'],
                'connected_calls': r['connected_calls'],
                'not_connected_calls': r['not_connected_calls'],
                'paid_calls':      r['paid_calls'],
                'live_calls':      0,
                'total_duration_sec': r['total_duration_sec'],
            })
        return result

    @staticmethod
    def get_buyer_performance(user: User, filters) -> list:
        qs = AnalyticsService._base_qs(user, filters)
        live_qs = AnalyticsService._live_qs(user, filters)
        live_counts = dict(live_qs.exclude(buyer_id=None).values_list('buyer_id').annotate(c=Count('id')))

        rows = (
            qs
            .exclude(buyer_id=None)
            .values('buyer_id', 'buyer_name')
            .annotate(
                total_calls=Count('id'),
                converted=Count('id', filter=Q(is_converted=True)),
                total_payout=Coalesce(Sum('dynamic_payout'), Decimal('0')),
                avg_bid=Coalesce(Avg('winning_bid'), Decimal('0')),
                
                repeat_answered=Count('id', filter=Q(status__in=[CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS]) & Q(is_duplicate=True)),
                connected_calls=Count('id', filter=Q(status__in=[
                    CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS,
                ])),
                not_connected_calls=Count('id', filter=~Q(status__in=[
                    CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS,
                ])),
                paid_calls=Count('id', filter=Q(
                    is_converted=True, campaign__payout_amount__gt=0,
                )),
                total_duration_sec=Coalesce(Sum('duration_seconds'), 0),
                avg_duration=Coalesce(Avg('duration_seconds'), 0.0),
            )
        )

        result = []
        for r in rows:
            bid = r['buyer_id']
            lc = live_counts.pop(bid, 0)
            total = r['total_calls'] + lc
            t_total = total or 1
            result.append({
                'buyer_id':       str(bid),
                'buyer_name':     r['buyer_name'],
                'total_calls':    total,
                'won_calls':      r['converted'],
                'avg_bid':        r['avg_bid'],
                'total_payout':   r['total_payout'],
                'avg_duration':   round(r['avg_duration'] or 0, 1),
                'conversion_rate': round((r['converted'] / t_total) * 100, 2),
                'duplicate_calls': r['repeat_answered'],
                'dupe': r['repeat_answered'],
                'duplicates': r['repeat_answered'],
                'connected_calls': r['connected_calls'],
                'not_connected_calls': r['not_connected_calls'],
                'paid_calls':      r['paid_calls'],
                'live_calls':      lc,
                'total_duration_sec': r['total_duration_sec'],
            })
            
        if live_counts:
            from buyers.models import Buyer
            buyers = {b.id: b.name for b in Buyer.objects.filter(id__in=live_counts.keys())}
            for bid, lc in live_counts.items():
                result.append({
                    'buyer_id': str(bid),
                    'buyer_name': buyers.get(bid, 'Unknown'),
                    'total_calls': lc,
                    'won_calls': 0,
                    'avg_bid': Decimal('0'),
                    'total_payout': Decimal('0'),
                    'avg_duration': 0.0,
                    'conversion_rate': 0.0,
                                    'connected_calls': lc,
                    'not_connected_calls': 0,
                    'paid_calls': 0,
                    'live_calls': lc,
                    'total_duration_sec': 0,
                    'duplicate_calls': 0,
                    'dupe': 0,
                    'duplicates': 0,
})
        result.sort(key=lambda x: x['total_calls'], reverse=True)
        return result

    # ── publisher performance ────────────────────────────────────────────────

    @staticmethod
    def get_publisher_performance(user: User, filters) -> list:
        qs = AnalyticsService._base_qs(user, filters)
        live_qs = AnalyticsService._live_qs(user, filters)
        live_counts = dict(live_qs.exclude(publisher_id=None).values_list('publisher_id').annotate(c=Count('id')))

        rows = (
            qs
            .exclude(publisher_id=None)
            .values('publisher_id', 'publisher_name')
            .annotate(
                total_calls=Count('id'),
                qualified_calls=Count('id', filter=Q(status__in=[CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS]) & ~Q(is_duplicate=True)),
                converted=Count('id', filter=Q(is_converted=True)),
                total_revenue=Coalesce(Sum('dynamic_revenue'), Decimal('0')),
                spam_count=Count('id', filter=Q(is_spam=True)),
                
                repeat_answered=Count('id', filter=Q(status__in=[CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS]) & Q(is_duplicate=True)),
                connected_calls=Count('id', filter=Q(status__in=[
                    CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS,
                ])),
                not_connected_calls=Count('id', filter=~Q(status__in=[
                    CallRecord.Status.COMPLETED, CallRecord.Status.IN_PROGRESS,
                ])),
                paid_calls=Count('id', filter=Q(
                    is_converted=True, campaign__payout_amount__gt=0,
                )),
                total_duration_sec=Coalesce(Sum('duration_seconds'), 0),
                avg_duration=Coalesce(Avg('duration_seconds', filter=~Q(status__in=['failed', 'no_answer', 'busy', 'canceled'])), 0.0),
            )
        )

        result = []
        for r in rows:
            pid = r['publisher_id']
            lc = live_counts.pop(pid, 0)
            total = r['total_calls'] + lc
            t_total = total or 1
            result.append({
                'publisher_id':    str(pid),
                'publisher_name':  r['publisher_name'],
                'total_calls':     total,
                'qualified_calls': r['qualified_calls'],
                'converted_calls': r['converted'],
                'conversion_rate': round((r['converted'] / t_total) * 100, 2),
                'total_revenue':   r['total_revenue'],
                'spam_rate':       round((r['spam_count'] / t_total) * 100, 2),
                'avg_duration':    round(r['avg_duration'] or 0, 1),
                'duplicate_calls': r['repeat_answered'],
                'dupe': r['repeat_answered'],
                'duplicates': r['repeat_answered'],
                'connected_calls': r['connected_calls'],
                'not_connected_calls': r['not_connected_calls'],
                'paid_calls':      r['paid_calls'],
                'live_calls':      lc,
                'total_duration_sec': r['total_duration_sec'],
            })

        if live_counts:
            from publishers.models import Publisher
            pubs = {p.id: p.name for p in Publisher.objects.filter(id__in=live_counts.keys())}
            for pid, lc in live_counts.items():
                result.append({
                    'publisher_id': str(pid),
                    'publisher_name': pubs.get(pid, 'Unknown'),
                    'total_calls': lc,
                    'qualified_calls': 0,
                    'converted_calls': 0,
                    'conversion_rate': 0.0,
                    'total_revenue': Decimal('0'),
                    'spam_rate': 0.0,
                    'avg_duration': 0.0,
                    'duplicate_calls': 0,
                    'dupe': 0,
                    'duplicates': 0,
                                    'connected_calls': lc,
                    'not_connected_calls': 0,
                    'paid_calls': 0,
                    'live_calls': lc,
                    'total_duration_sec': 0,
                    'duplicate_calls': 0,
                    'dupe': 0,
                    'duplicates': 0,
})
        result.sort(key=lambda x: x['total_calls'], reverse=True)
        return result

    # ── call log ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_call_log(user: User, filters) -> dict:
        from routing.models import CallLog
        hist_qs = AnalyticsService._base_qs(user, filters).order_by('-created_at')
        live_qs = AnalyticsService._live_qs(user, filters).order_by('-created_at')

        total = hist_qs.count() + live_qs.count()
        
        offset = getattr(filters, 'offset', 0)
        limit = getattr(filters, 'limit', 50)
        fetch_limit = offset + limit
        
        hist_items = list(hist_qs[:fetch_limit])
        live_items = list(live_qs[:fetch_limit])

        sids = [r.twilio_call_sid for r in hist_items if r.twilio_call_sid]
        dest_map = dict(
            CallLog.objects.filter(twilio_call_sid__in=sids)
            .values_list('twilio_call_sid', 'destination_number')
        )

        hist_formatted = [AnalyticsService._format_record(r, dest_map.get(r.twilio_call_sid)) for r in hist_items]
        live_formatted = [AnalyticsService._format_live_log(r) for r in live_items]

        combined = sorted(hist_formatted + live_formatted, key=lambda x: x['created_at'], reverse=True)
        items = combined[offset : fetch_limit]

        return {
            'total':  total,
            'offset': offset,
            'limit':  limit,
            'items':  items,
        }

    @staticmethod
    def _format_live_log(r) -> dict:
        dt_start = r.created_at
        return {
            'id':               str(r.id),
            'twilio_call_sid':  r.twilio_call_sid,
            'caller_number':    (lambda rc: (d:=''.join(filter(str.isdigit, rc or ''))) and (d[1:] if d.startswith('1') and len(d)==11 else d))(r.caller_number),
            'caller_state':     r.caller_state,
            'called_number':    r.called_number,
            'destination_number': r.destination_number,
            'destinationNumber':  r.destination_number,
            'campaign_id':      str(r.campaign_id) if r.campaign_id else None,
            'campaign_name':    r.campaign.name if r.campaign else '',
            'buyer_id':         str(r.buyer_id) if r.buyer_id else None,
            'buyer_name':       r.buyer.name if r.buyer else '',
            'publisher_id':     str(r.publisher_id) if r.publisher_id else None,
            'publisher_name':   r.publisher.name if r.publisher else '',
            'status':           r.status.replace('_', '-'),
            'duration_seconds': 0,
            'is_converted':     False,
            'is_duplicate':     getattr(r, 'is_duplicate', False),
            'is_spam':          False,
            'revenue':          0,
            'payout':           0,
            'profit':           0,
            'winning_bid':      None,
            'recording_url':    '',
            'started_at':       dt_start,
            'startedAt':        int(dt_start.timestamp() * 1000) if dt_start else None,
            'ended_at':         None,
            'created_at':       r.created_at,
            'ipqs_line_type':   r.ipqs_line_type,
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
            # Was absent entirely, so any client counting qualified calls from
            # the call log had no field to read.
            'is_qualified':     r.is_qualified,
            'is_duplicate':     r.is_duplicate,
            'is_spam':          r.is_spam,
            'revenue':          r.dynamic_revenue,
            'payout':           r.dynamic_payout,
            'profit':           r.dynamic_profit,
            'winning_bid':      r.winning_bid,
            'recording_url':    public_recording_url(r.recording_url),
            'carrier_name':     r.carrier_name,
            'carrier':          r.carrier or 'Unknown',
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
        
        # Qualified and Duplicate were absent, so the two columns most often
        # queried against this export could not be checked from it at all.
        # Call ID lets a row be matched back to the call detail view.
        yield writer.writerow([
            'Date', 'Call ID', 'Caller', 'State', 'Carrier', 'Called Number',
            'Campaign', 'Buyer', 'Publisher',
            'Status', 'Duration (s)', 'Qualified', 'Converted', 'Duplicate',
            'Revenue', 'Payout', 'Profit', 'Recording'
        ])

        for r in qs.iterator(chunk_size=2000):
            raw_caller = r.caller_number or ''
            clean_caller = raw_caller.lstrip('+')
            if clean_caller.startswith('1') and len(clean_caller) == 11:
                clean_caller = clean_caller[1:]
            yield writer.writerow([
                r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                str(r.id),
                clean_caller, r.caller_state, r.carrier or '', r.called_number,
                r.campaign_name, r.buyer_name, r.publisher_name,
                r.status, r.duration_seconds,
                'Yes' if r.is_qualified else 'No',
                'Yes' if r.is_converted else 'No',
                'Yes' if r.is_duplicate else 'No',
                r.dynamic_revenue, r.dynamic_payout, r.dynamic_profit,
                public_recording_url(r.recording_url),
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