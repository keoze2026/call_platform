"""Asterisk handler — public endpoints called by Asterisk AGI/dialplan.

Server-to-server calls (no JWT auth). Secured by a shared secret header.
"""
import hmac
import json
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
    try:
        call_log = CallLog.objects.create(
            organization=campaign.organization,
            campaign=campaign,
            publisher=phone.publisher,
            publisher_payout=0,
            caller_number=caller,
            called_number=called,
            twilio_call_sid=call_sid,
            status=CallLog.Status.RINGING,
        )
    except IntegrityError:
        # Duplicate SID (carrier retry) — reuse the existing call log instead of crashing
        call_log = CallLog.objects.filter(twilio_call_sid=call_sid).first()
        if call_log is None:
            return JsonResponse({"action": "hangup", "reason": "duplicate_call"})

    # IPQualityScore spam/VOIP check
    if getattr(campaign, 'ipqs_enabled', False):
        from spam_protection.telnyx import TelnyxLookupService
        telnyx_result = TelnyxLookupService.check_phone(caller)
        call_log.ipqs_checked = True
        call_log.ipqs_fraud_score = telnyx_result.get('fraud_score', 0) or 0
        call_log.ipqs_is_voip = telnyx_result.get('VOIP', False) or False
        call_log.ipqs_line_type = telnyx_result.get('line_type', '') or ''
        
        should_block, reason = TelnyxLookupService.should_block(telnyx_result, campaign)
        if should_block:
            call_log.ipqs_block_reason = reason
            call_log.status = CallLog.Status.FAILED
            call_log.ended_at = timezone.now()
            return JsonResponse({"action": "hangup", "reason": reason})
    call_log.status = CallLog.Status.IN_PROGRESS
    call_log.save()

    decision = RoutingEngine.route_call(str(campaign.id), {
        'caller_number': caller,
        'twilio_call_sid': call_log.twilio_call_sid,
    })

    if not decision or decision.get('error') or not decision.get('destination'):
        call_log.status = CallLog.Status.FAILED
        call_log.ended_at = timezone.now()
        return JsonResponse({
            "action": "hangup",
            "reason": decision.get('error', 'no_destination') if decision else 'no_decision'
        })

    buyer = decision.get('buyer')
    dest_number = decision.get('destination')
    if buyer:
        call_log.buyer = buyer
        # Resolve dynamic live destination from UI
        try:
            from buyers.destination import Destination
            live_dest = Destination.objects.filter(buyer=buyer, enabled=True).order_by('-created_at').first()
            if live_dest and live_dest.tfn:
                dest_number = live_dest.tfn
        except Exception:
            pass
        call_log.destination_number = dest_number
    else:
        call_log.destination_number = dest_number

    call_log.status = CallLog.Status.IN_PROGRESS
    call_log.save()

    return JsonResponse({
        "action": "dial",
        "buyer_number": dest_number,
        "call_log_id": str(call_log.id),
        "max_duration": 3600,
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
    payout_val = (getattr(campaign, 'payout_amount', 0) or 0) if converted else 0

    call_log.revenue = payout_val
    call_log.publisher_payout = payout_val
    call_log.save()

    # Sync to analytics CallRecord
    try:
        from analytics.models import CallRecord
        cr, created = CallRecord.objects.update_or_create(
            twilio_call_sid=call_log.twilio_call_sid,
            organization=call_log.organization,
            defaults={
                'caller_number': call_log.caller_number,
                'called_number': call_log.called_number,
                'status': 'completed' if answered else 'no_answer',
                'duration_seconds': duration,
                'is_converted': converted,
                'revenue': payout_val,
                'payout': payout_val,
                'profit': 0,
                'campaign_id': call_log.campaign_id,
                'campaign_name': call_log.campaign.name if call_log.campaign else '',
                'buyer_id': call_log.buyer_id,
                'buyer_name': call_log.buyer.name if call_log.buyer else '',
                'publisher_id': call_log.publisher_id,
                'publisher_name': call_log.publisher.name if call_log.publisher else '',
                'recording_url': recording_url if recording_url else '',
            }
        )
    except Exception:
        pass

    return JsonResponse({"received": True, "call_log_id": str(call_log.id), "converted": converted})
