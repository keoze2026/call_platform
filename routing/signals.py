"""
routing/signals.py

Keeps analytics.CallRecord in sync with routing.CallLog.

Nothing else in the codebase currently creates CallRecord rows, which is why
the dashboard (which reads from CallRecord) shows zero calls even though
CallLog is populated correctly by the Asterisk webhook.

This listens for CallLog saves and mirrors completed/terminal-status calls
into CallRecord. It does not modify CallLog or anything upstream of it.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import CallLog
from analytics.models import CallRecord

TERMINAL_STATUSES = {
    CallLog.Status.COMPLETED,
    CallLog.Status.NO_ANSWER,
    CallLog.Status.BUSY,
    CallLog.Status.FAILED,
}

STATUS_MAP = {
    CallLog.Status.RINGING: CallRecord.Status.IN_PROGRESS,
    CallLog.Status.IN_PROGRESS: CallRecord.Status.IN_PROGRESS,
    CallLog.Status.COMPLETED: CallRecord.Status.COMPLETED,
    CallLog.Status.NO_ANSWER: CallRecord.Status.NO_ANSWER,
    CallLog.Status.BUSY: CallRecord.Status.BUSY,
    CallLog.Status.FAILED: CallRecord.Status.FAILED,
}


@receiver(post_save, sender=CallLog)
def sync_call_record(sender, instance: CallLog, created, **kwargs):
    call = instance

    if call.status not in TERMINAL_STATUSES:
        return

    CallRecord.objects.update_or_create(
        id=call.id,
        defaults={
            'organization': call.organization,
            'twilio_call_sid': call.twilio_call_sid or '',
            'caller_number': call.caller_number,
            'caller_state': call.caller_state or '',
            'called_number': call.called_number,
            'campaign_id': call.campaign_id,
            'campaign_name': call.campaign.name if call.campaign_id else '',
            'buyer_id': call.buyer_id,
            'buyer_name': call.buyer.name if call.buyer_id else '',
            'publisher_id': call.publisher_id,
            'publisher_name': call.publisher.name if call.publisher_id else '',
            'status': STATUS_MAP.get(call.status, CallRecord.Status.FAILED),
            'duration_seconds': call.duration or 0,
            'billable_seconds': call.duration or 0,
            'recording_url': call.recording_url or '',
            'revenue': call.revenue or 0,
            'payout': call.buyer_payout or 0,
            'profit': (call.revenue or 0) - (call.buyer_payout or 0),
            'answered_at': call.answered_at,
            'ended_at': call.ended_at,
        },
    )
