from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, Sum, Avg, Q
from decimal import Decimal
import hashlib


def make_id(*args):
    return hashlib.md5(':'.join(str(a) for a in args).encode()).hexdigest()[:12]


class AIInsightsService:

    @classmethod
    def get_recommendations(cls, organization):
        recommendations = []
        today = timezone.now().date()
        last_7_days = today - timedelta(days=7)

        from routing.models import CallLog
        from buyers.models import Buyer
        from campaigns.models import Campaign

        buyers = Buyer.objects.filter(organization=organization, status='active')
        for buyer in buyers:
            logs = CallLog.objects.filter(buyer=buyer, created_at__date__gte=last_7_days)
            total = logs.count()
            if total > 0:
                no_answer = logs.filter(status='no_answer').count()
                no_answer_rate = (no_answer / total) * 100
                if no_answer_rate > 50:
                    recommendations.append({
                        'id': make_id('buyer', buyer.id, 'no_answer'),
                        'type': 'warning',
                        'category': 'buyer',
                        'title': f'Buyer {buyer.name} has high no-answer rate',
                        'message': f'{no_answer_rate:.0f}% of calls are not answered. Consider pausing this buyer.',
                        'action': 'pause_buyer',
                        'entity_id': str(buyer.id),
                    })

        campaigns = Campaign.objects.filter(organization=organization, status='active')
        for campaign in campaigns:
            if not campaign.campaign_assignments.filter(is_active=True).exists():
                recommendations.append({
                    'id': make_id('campaign', campaign.id, 'no_buyers'),
                    'type': 'error',
                    'category': 'campaign',
                    'title': f'Campaign {campaign.name} has no active buyers',
                    'message': 'Calls will fail — no buyers assigned to this campaign.',
                    'action': 'assign_buyer',
                    'entity_id': str(campaign.id),
                })

        today_calls = CallLog.objects.filter(organization=organization, created_at__date=today)
        total_today = today_calls.count()
        if total_today > 10:
            blocked = today_calls.filter(status='failed', ipqs_checked=True).count()
            block_rate = (blocked / total_today) * 100
            if block_rate > 30:
                recommendations.append({
                    'id': make_id('spam', organization.id, today),
                    'type': 'warning',
                    'category': 'spam',
                    'title': 'High spam rate detected today',
                    'message': f'{block_rate:.0f}% of calls are being blocked as spam.',
                    'action': 'review_spam_settings',
                    'entity_id': None,
                })

        top_campaign = CallLog.objects.filter(
            organization=organization,
            created_at__date__gte=last_7_days,
            status='completed'
        ).values('campaign__name', 'campaign__id').annotate(
            revenue=Sum('revenue')
        ).order_by('-revenue').first()

        if top_campaign:
            recommendations.append({
                'id': make_id('revenue', top_campaign['campaign__id'], 'top'),
                'type': 'success',
                'category': 'revenue',
                'title': f'Top performing campaign: {top_campaign["campaign__name"]}',
                'message': f'Generated ${top_campaign["revenue"]:.2f} in the last 7 days.',
                'action': 'view_campaign',
                'entity_id': str(top_campaign['campaign__id']),
            })

        return recommendations

    @classmethod
    def get_anomalies(cls, organization):
        anomalies = []
        now = timezone.now()
        today = now.date()
        yesterday = today - timedelta(days=1)

        from routing.models import CallLog

        today_calls = CallLog.objects.filter(organization=organization, created_at__date=today).count()
        yesterday_calls = CallLog.objects.filter(organization=organization, created_at__date=yesterday).count()

        if yesterday_calls > 0:
            change = ((today_calls - yesterday_calls) / yesterday_calls) * 100
            if change < -50:
                anomalies.append({
                    'type': 'critical',
                    'title': 'Significant drop in call volume',
                    'message': f'Calls dropped by {abs(change):.0f}% compared to yesterday.',
                    'metric': 'call_volume',
                    'value': float(today_calls),
                    'previous': float(yesterday_calls),
                    'change_percent': round(change, 2),
                })
            elif change > 100:
                anomalies.append({
                    'type': 'info',
                    'title': 'Unusual spike in call volume',
                    'message': f'Calls increased by {change:.0f}% compared to yesterday.',
                    'metric': 'call_volume',
                    'value': float(today_calls),
                    'previous': float(yesterday_calls),
                    'change_percent': round(change, 2),
                })

        today_revenue = CallLog.objects.filter(organization=organization, created_at__date=today).aggregate(total=Sum('revenue'))['total'] or 0
        yesterday_revenue = CallLog.objects.filter(organization=organization, created_at__date=yesterday).aggregate(total=Sum('revenue'))['total'] or 0

        if yesterday_revenue > 0:
            rev_change = ((float(today_revenue) - float(yesterday_revenue)) / float(yesterday_revenue)) * 100
            if rev_change < -40:
                anomalies.append({
                    'type': 'critical',
                    'title': 'Revenue drop detected',
                    'message': f'Revenue dropped by {abs(rev_change):.0f}% compared to yesterday.',
                    'metric': 'revenue',
                    'value': float(today_revenue),
                    'previous': float(yesterday_revenue),
                    'change_percent': round(rev_change, 2),
                })

        hour = now.hour
        if 8 <= hour <= 20:
            last_hour_calls = CallLog.objects.filter(organization=organization, created_at__gte=now - timedelta(hours=1)).count()
            if last_hour_calls == 0 and today_calls > 0:
                anomalies.append({
                    'type': 'warning',
                    'title': 'No calls in the last hour',
                    'message': 'No calls received in the last hour during business hours.',
                    'metric': 'live_activity',
                    'value': 0.0,
                    'previous': None,
                    'change_percent': None,
                })

        return anomalies

    @classmethod
    def autopilot_action(cls, organization):
        actions_taken = []
        last_24h = timezone.now() - timedelta(hours=24)

        from routing.models import CallLog
        from buyers.models import Buyer

        buyers = Buyer.objects.filter(organization=organization, status='active')
        for buyer in buyers:
            logs = CallLog.objects.filter(buyer=buyer, created_at__gte=last_24h)
            total = logs.count()
            if total >= 10:
                no_answer = logs.filter(status='no_answer').count()
                no_answer_rate = (no_answer / total) * 100
                if no_answer_rate > 70:
                    buyer.status = 'paused'
                    buyer.save()
                    actions_taken.append({
                        'action': 'paused_buyer',
                        'entity': buyer.name,
                        'reason': f'No-answer rate {no_answer_rate:.0f}% exceeded 70% threshold',
                    })

        return actions_taken
