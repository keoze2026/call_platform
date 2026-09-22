"""
routing/signals.py

Keeps analytics.CallRecord in sync with routing.CallLog.

Nothing else in the codebase currently creates CallRecord rows, which is why
the dashboard (which reads from CallRecord) shows zero calls even though
CallLog is populated correctly by the Asterisk webhook.

This listens for CallLog saves and mirrors completed/terminal-status calls
into CallRecord. It does not modify CallLog or anything upstream of it.

The mirroring itself runs in a Celery worker, not in the request thread — the
receiver only enqueues. Enqueueing happens on transaction commit so the worker
can never read a CallLog row that has not been written yet.
"""
from decimal import Decimal

from django.db import transaction
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


def _is_converted(call) -> bool:
    """Answered, and at least as long as the campaign requires."""
    if call.status != CallLog.Status.COMPLETED:
        return False
    threshold = getattr(call.campaign, 'min_call_duration', 0) if call.campaign_id else 0
    return (call.duration or 0) >= (threshold or 0)


def _campaign_revenue(call) -> Decimal:
    """Revenue for a call: campaign pricing, falling back to the stored value."""
    if call.campaign_id and call.campaign:
        return Decimal(call.campaign.revenue_amount or 0) or Decimal(call.revenue or 0)
    return Decimal(call.revenue or 0)


def _campaign_payout(call) -> Decimal:
    """Payout for a call: campaign pricing, falling back to the stored value."""
    if call.campaign_id and call.campaign:
        return Decimal(call.campaign.payout_amount or 0) or Decimal(call.publisher_payout or 0)
    return Decimal(call.publisher_payout or 0)


def mirror_call_log(call_log_id) -> bool:
    """Mirror one CallLog into CallRecord. Safe to call from a Celery worker.

    Returns True if a CallRecord was written, False if the call is gone or is
    not in a terminal status (re-checked here because the row may have changed
    between enqueue and execution).
    """
    try:
        call = CallLog.objects.select_related(
            'organization', 'campaign', 'buyer', 'publisher'
        ).get(id=call_log_id)
    except CallLog.DoesNotExist:
        return False

    if call.status not in TERMINAL_STATUSES:
        return False

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
            'is_converted': _is_converted(call),
            # Qualified means an answered call from a new caller: answered, and
            # not a repeat inside the campaign's duplicate window. The Qualified
            # column counts exactly these, so the drill-down lists the same
            # calls the column reports.
            'is_qualified': (
                call.status == CallLog.Status.COMPLETED and not call.is_duplicate
            ),
            'billable_seconds': call.duration or 0,
            'is_duplicate': call.is_duplicate,
            'recording_url': call.recording_url or '',
            'carrier_name': call.carrier_name or '',
            'carrier': call.carrier or '',
            'ipqs_line_type': call.ipqs_line_type or '',
            'revenue': _campaign_revenue(call),
            # publisher_payout, not buyer_payout: publisher payout is what
            # call_ended writes and what 'payout' means in reporting.
            'payout': _campaign_payout(call),
            'profit': _campaign_revenue(call) - _campaign_payout(call),
            'answered_at': call.answered_at,
            'ended_at': call.ended_at,
        },
    )
    return True


@receiver(post_save, sender=CallLog)
def sync_call_record(sender, instance: CallLog, created, **kwargs):
    if instance.status not in TERMINAL_STATUSES:
        return

    call_log_id = str(instance.id)

    def _enqueue():
        try:
            from tasks import mirror_call_record
            mirror_call_record.delay(call_log_id)
        except Exception:
            # Broker unreachable — mirror inline rather than lose the record
            mirror_call_log(call_log_id)

    transaction.on_commit(_enqueue)
