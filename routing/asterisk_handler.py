"""Asterisk handler — public endpoints called by Asterisk AGI/dialplan.

Server-to-server calls (no JWT auth). Secured by a shared secret header.
"""
import hmac
import json
import logging
import uuid
from django.db import IntegrityError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.utils import timezone

from phone_numbers.models import PhoneNumber
from routing.models import CallLog
from routing.engine import RoutingEngine

logger = logging.getLogger(__name__)


def _check_secret(request):
    secret = request.headers.get('X-Asterisk-Secret', '')
    expected = getattr(settings, 'ASTERISK_SHARED_SECRET', '')
    return bool(secret and expected and hmac.compare_digest(secret, expected))


@csrf_exempt
@require_http_methods(["POST"])
def route_incoming_call(request):
    if not _check_secret(request):
        return JsonResponse({"error": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"error": "Bad JSON"}, status=400)

    caller = data.get('caller_number', '')
    called = data.get('called_number', '')
    asterisk_call_id = data.get('asterisk_call_id', '')

    if not caller or not called:
        return JsonResponse({"error": "Missing fields"}, status=400)

    try:
        phone = PhoneNumber.objects.select_related('campaign', 'publisher').get(
            number=called, status='active'
        )
    except PhoneNumber.DoesNotExist:
        return JsonResponse({"action": "hangup", "reason": "number_not_found"})

    campaign = phone.campaign
    if not campaign or campaign.status != 'active':
        return JsonResponse({"action": "hangup", "reason": "campaign_inactive"})

    call_sid = asterisk_call_id or f"asterisk-{uuid.uuid4()}"

    # Has this caller reached this campaign before, inside its window? Checked
    # before the row is created so the new call is not counted against itself.
    # Recorded regardless of duplicate_call_block — that flag decides whether a
    # duplicate is blocked, not whether it is reported.
    is_duplicate = RoutingEngine.is_duplicate(
        caller, str(campaign.id), campaign.duplicate_call_block_hours or 24
    )

    try:
        call_log = CallLog.objects.create(
            organization=campaign.organization,
            campaign=campaign,
            publisher=phone.publisher,
            publisher_payout=0,
            caller_number=caller,
            called_number=called,
            twilio_call_sid=call_sid,
            is_duplicate=is_duplicate,
            status=CallLog.Status.RINGING,
        )
    except IntegrityError:
        # Duplicate SID (carrier retry) — reuse the existing call log instead of crashing
        call_log = CallLog.objects.filter(twilio_call_sid=call_sid).first()
        if call_log is None:
            return JsonResponse({"action": "hangup", "reason": "duplicate_call"})

    # Carrier lookup runs in a worker, not here. It is a blocking HTTP request
    # with a 5s timeout and nothing about routing depends on its result, so
    # keeping it off the call path removes an external round-trip per call.
    try:
        from tasks import enrich_call_carrier
        enrich_call_carrier.delay(str(call_log.id), caller)
    except Exception:
        logger.warning("carrier_enrichment_not_queued: call_log=%s", call_log.id)

    decision = RoutingEngine.route_call(str(campaign.id), {
        'caller_number': caller,
        'twilio_call_sid': call_log.twilio_call_sid,
        # Carries the tracking number's own payout_per_call into the balance
        # check, so per-number pricing is honoured over the campaign default.
        'phone_number': phone,
    })

    if not decision or decision.get('error') or not decision.get('destination'):
        reason = decision.get('error', 'no_destination') if decision else 'no_decision'
        call_log.status = CallLog.Status.FAILED
        call_log.block_reason = reason[:100]
        call_log.ended_at = timezone.now()
        call_log.save(update_fields=['status', 'block_reason', 'ended_at', 'updated_at'])
        return JsonResponse({"action": "hangup", "reason": reason})

    buyer = decision.get('buyer')
    dest_number = decision.get('destination')
    if buyer:
        call_log.buyer = buyer
        # Resolve dynamic live destination from UI
        try:
            from buyers.destination import Destination
            live_dest = Destination.objects.filter(
                buyer=buyer, enabled=True
            ).only('tfn').order_by('-created_at').first()
            if live_dest and live_dest.tfn:
                dest_number = live_dest.tfn
        except Exception:
            pass
        call_log.destination_number = dest_number
    else:
        call_log.destination_number = dest_number

    call_log.status = CallLog.Status.IN_PROGRESS
    call_log.save(update_fields=['status', 'buyer', 'destination_number', 'updated_at'])

    return JsonResponse({
        "action": "dial",
        "buyer_number": dest_number,
        "call_log_id": str(call_log.id),
        "max_duration": 3600,
    })


@csrf_exempt
@require_http_methods(["POST"])
def active_channels(request):
    """Reconcile live call rows against the channels Asterisk actually has.

    A call whose end-of-call webhook never arrived sits in in_progress forever.
    Asterisk is the authority on what is really up, so a small host script posts
    its channel list here and anything the platform thinks is live but Asterisk
    does not have gets closed.

    Deliberately conservative, because closing a real call would be far worse
    than leaving a stale row:

      * rows younger than ASTERISK_SYNC_GRACE_SECONDS are never touched - a call
        can exist here a moment before Asterisk reports the channel
      * when Asterisk reports zero channels, nothing can be live, so every row
        past the grace period is closed
      * when Asterisk reports channels, ids are only trusted if at least one of
        them matches a live row. If none match, the id format differs from what
        the dialplan sends and NOTHING is closed - better to do nothing than
        guess

    Reads nothing in the routing path and changes no routing behaviour.
    """
    if not _check_secret(request):
        return JsonResponse({"error": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"error": "Bad JSON"}, status=400)

    active_ids = [str(i) for i in (data.get('active_call_ids') or [])]
    try:
        active_count = int(data.get('active_count', len(active_ids)))
    except (TypeError, ValueError):
        return JsonResponse({"error": "active_count must be a number"}, status=400)

    grace = getattr(settings, 'ASTERISK_SYNC_GRACE_SECONDS', 120)
    cutoff = timezone.now() - timezone.timedelta(seconds=grace)

    live = CallLog.objects.filter(
        status__in=[CallLog.Status.RINGING, CallLog.Status.IN_PROGRESS],
        created_at__lt=cutoff,
    )

    if active_count == 0:
        stale = list(live)
        reason = 'asterisk reports no active channels'
    else:
        live_sids = set(live.values_list('twilio_call_sid', flat=True))
        if not (live_sids & set(active_ids)):
            # No overlap: the ids Asterisk sends are not the ids stored here, so
            # absence from the list proves nothing. Leave everything alone.
            return JsonResponse({
                "closed": 0,
                "skipped": live.count(),
                "reason": "channel ids do not match stored call ids; nothing closed",
            })
        stale = [c for c in live if c.twilio_call_sid not in active_ids]
        reason = 'not present in asterisk channel list'

    closed = 0
    now = timezone.now()
    for call in stale:
        call.status = CallLog.Status.NO_ANSWER
        call.ended_at = now
        call.block_reason = 'asterisk_gone'
        # Saved one at a time so post_save fires and the call reaches CallRecord
        call.save(update_fields=['status', 'ended_at', 'block_reason', 'updated_at'])
        logger.info('closed orphaned call %s (%s)', call.id, reason)
        closed += 1

    return JsonResponse({
        "closed": closed,
        "asterisk_active": active_count,
        "reason": reason,
    })


@csrf_exempt
@require_http_methods(["POST"])
def call_ended(request):
    if not _check_secret(request):
        return JsonResponse({"error": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"error": "Bad JSON"}, status=400)

    call_log_id = data.get('call_log_id', '')
    duration = int(data.get('duration', 0))
    answered = bool(data.get('answered', False))
    recording_url = (
    data.get('recording_url', '') or 
    data.get('RecordingUrl', '') or 
    data.get('recording', '') or 
    data.get('media_url', '') or 
    data.get('audio_url', '') or 
    data.get('file_url', '') or 
    data.get('url', '')
)

    if not call_log_id:
        return JsonResponse({"error": "Missing call_log_id"}, status=400)

    try:
        call_log = CallLog.objects.select_related('campaign', 'buyer').get(id=call_log_id)
    except CallLog.DoesNotExist:
        return JsonResponse({"error": "Call not found"}, status=404)

    call_log.duration = duration
    call_log.status = CallLog.Status.COMPLETED if answered else CallLog.Status.NO_ANSWER
    call_log.ended_at = timezone.now()
    if answered and not call_log.answered_at:
        call_log.answered_at = timezone.now() - timezone.timedelta(seconds=duration)

    if recording_url:
        call_log.recording_url = recording_url

    campaign = call_log.campaign
    min_dur = getattr(campaign, 'min_call_duration', 0) if campaign else 0
    converted = answered and (duration >= min_dur)

    # Revenue and payout are different numbers: revenue is what the buyer pays
    # for the call, payout is what goes to the publisher. Writing the payout into
    # both left every CallLog with zero profit.
    phone = PhoneNumber.objects.filter(number=call_log.called_number).first()
    payout_val = RoutingEngine.required_call_balance(campaign, phone) if converted else 0
    revenue_val = (getattr(campaign, 'revenue_amount', 0) or 0) if converted else 0

    call_log.revenue = revenue_val
    call_log.publisher_payout = payout_val
    call_log.save()

    # CallRecord is written by the post_save signal on routing/signals.py, keyed
    # on the CallLog id. A second update_or_create here keyed on twilio_call_sid
    # created a *separate* row for every terminal call, doubling every analytics
    # total — 8 real calls reported as 16.

    # Deduct the call's cost from the organization's balance. Charged only on a
    # converted call, at the same rate RoutingEngine checked before dispatch, so
    # a call that was allowed through is always affordable. charge_call is
    # idempotent on call_sid, so a carrier webhook retry will not double-charge.
    if converted and getattr(settings, 'CHARGE_COMPLETED_CALLS', True):
        try:
            from billing.services import BillingService

            # Per-minute pricing: ceil(duration / 60) x rate x (1 + markup).
            # A missed call has no duration and therefore no cost.
            amount = BillingService.call_cost(call_log.organization, duration)

            if amount > 0:
                charge = BillingService.charge_call(
                    organization=call_log.organization,
                    campaign=campaign,
                    buyer=call_log.buyer,
                    publisher=call_log.publisher,
                    amount=amount,
                    call_sid=call_log.twilio_call_sid,
                )
                if charge is None:
                    # Balance ran out between dispatch and hangup (concurrent
                    # calls draining the same account). The call already
                    # happened; record it so it can be reconciled.
                    logger.warning(
                        "call_charge_failed: call_log=%s org=%s amount=%s balance=%s",
                        call_log.id,
                        call_log.organization_id,
                        amount,
                        BillingService.get_balance(call_log.organization),
                    )
        except Exception:
            logger.exception("call_charge_error: call_log=%s", call_log.id)

    return JsonResponse({"received": True, "call_log_id": str(call_log.id), "converted": converted})
