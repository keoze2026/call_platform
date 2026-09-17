import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from routing.models import CallLog
from analytics.models import CallRecord

print("Starting backfill sync...")
count = 0
for log in CallLog.objects.exclude(status__in=['ringing', 'in_progress']):
    cr, created = CallRecord.objects.get_or_create(
        twilio_call_sid=log.twilio_call_sid,
        organization=log.organization,
        defaults={
            'caller_number': log.caller_number,
            'called_number': log.called_number,
            'status': log.status,
            'duration_seconds': log.duration,
            'campaign_id': log.campaign_id,
            'campaign_name': log.campaign.name if log.campaign else '',
            'buyer_id': log.buyer_id,
            'buyer_name': log.buyer.name if log.buyer else '',
            'publisher_id': log.publisher_id,
            'publisher_name': log.publisher.name if log.publisher else '',
            'revenue': log.revenue,
            'payout': log.buyer_payout or log.publisher_payout,
            'profit': log.revenue - (log.buyer_payout or log.publisher_payout),
            'is_converted': (log.status == 'completed') and (log.duration >= (getattr(log.campaign, 'min_call_duration', 0) if log.campaign else 0)),
            'is_qualified': (log.status == 'completed') and (log.duration >= (getattr(log.campaign, 'min_call_duration', 0) if log.campaign else 0)),
            'recording_url': log.recording_url,
            'caller_state': log.caller_state,
            'created_at': log.created_at,
            'started_at': log.created_at,
            'ended_at': log.ended_at,
            'ipqs_line_type': log.ipqs_line_type
        }
    )
    if created:
        count += 1

print(f"Sync complete. Created {count} missing CallRecords.")
