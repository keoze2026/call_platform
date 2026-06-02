from django.db.models import Sum, Count
from django.utils import timezone
from datetime import timedelta
from routing.models import CallLog
from billing.models import BillingAccount
from campaigns.models import Campaign
from buyers.models import Buyer


def dashboard_callback(request, context):
    today = timezone.now().date()
    today_start = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.min.time())
    )

    # --- Calls Today ---
    calls_today = CallLog.objects.filter(created_at__gte=today_start)
    total_calls_today = calls_today.count()
    completed_calls_today = calls_today.filter(status='completed').count()
    failed_calls_today = calls_today.filter(status='failed').count()
    no_answer_today = calls_today.filter(status='no_answer').count()
    live_calls = calls_today.filter(status='in_progress').count()

    # --- Revenue Today ---
    revenue_today = calls_today.aggregate(
        total=Sum('revenue')
    )['total'] or 0

    buyer_payout_today = calls_today.aggregate(
        total=Sum('buyer_payout')
    )['total'] or 0

    publisher_payout_today = calls_today.aggregate(
        total=Sum('publisher_payout')
    )['total'] or 0

    profit_today = revenue_today - buyer_payout_today - publisher_payout_today

    # --- Blocked Today ---
    blocked_today = calls_today.filter(
        status='failed',
        ipqs_checked=True
    ).count()

    # --- Last 7 Days Call Volume ---
    last_7_days = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_start = timezone.make_aware(
            timezone.datetime.combine(day, timezone.datetime.min.time())
        )
        day_end = timezone.make_aware(
            timezone.datetime.combine(day + timedelta(days=1), timezone.datetime.min.time())
        )
        count = CallLog.objects.filter(
            created_at__gte=day_start,
            created_at__lt=day_end
        ).count()
        last_7_days.append({'day': day.strftime('%a'), 'count': count})

    # --- Top Campaigns Today ---
    top_campaigns = calls_today.values(
        'campaign__name'
    ).annotate(
        total=Count('id'),
        revenue=Sum('revenue')
    ).order_by('-total')[:5]

    # --- Top Buyers Today ---
    top_buyers = calls_today.filter(
        buyer__isnull=False
    ).values(
        'buyer__name'
    ).annotate(
        total=Count('id'),
        payout=Sum('buyer_payout')
    ).order_by('-total')[:5]

    # --- Active Campaigns ---
    active_campaigns = Campaign.objects.filter(status='active').count()
    total_campaigns = Campaign.objects.count()

    # --- Active Buyers ---
    active_buyers = Buyer.objects.filter(status='active').count()
    total_buyers = Buyer.objects.count()

    # --- Total Balance Across All Billing Accounts ---
    total_balance = BillingAccount.objects.aggregate(
        total=Sum('balance')
    )['total'] or 0

    context.update({
        'total_calls_today': total_calls_today,
        'completed_calls_today': completed_calls_today,
        'failed_calls_today': failed_calls_today,
        'no_answer_today': no_answer_today,
        'live_calls': live_calls,
        'blocked_today': blocked_today,
        'revenue_today': round(revenue_today, 2),
        'buyer_payout_today': round(buyer_payout_today, 2),
        'publisher_payout_today': round(publisher_payout_today, 2),
        'profit_today': round(profit_today, 2),
        'last_7_days': last_7_days,
        'top_campaigns': top_campaigns,
        'top_buyers': top_buyers,
        'active_campaigns': active_campaigns,
        'total_campaigns': total_campaigns,
        'active_buyers': active_buyers,
        'total_buyers': total_buyers,
        'total_balance': round(total_balance, 2),
    })

    return context