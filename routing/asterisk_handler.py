"""Asterisk handler — public endpoints called by Asterisk AGI/dialplan.

Server-to-server calls (no JWT auth). Secured by a shared secret header.
"""
import json
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
    return bool(secret and expected and secret == expected)


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
        phone = PhoneNumber.objects.select_related('campaign').get(
            number=called, status='active'
        )
    except PhoneNumber.DoesNotExist:
        return JsonResponse({"action": "hangup", "reason": "number_not_found"})

    campaign = phone.campaign
    if not campaign or campaign.status != 'active':
        return JsonResponse({"action": "hangup", "reason": "campaign_inactive"})

    call_log = CallLog.objects.create(
        organization=campaign.organization,
        campaign=campaign,
        caller_number=caller,
        called_number=called,
        twilio_call_sid=asterisk_call_id or f"asterisk-{timezone.now().timestamp()}",
        status=CallLog.Status.RINGING,
    )

    # IPQualityScore spam/VOIP check
    if getattr(campaign, 'ipqs_enabled', False):
        from spam_protection.ipqs import IPQSService
        ipqs_result = IPQSService.check_phone(caller)
        call_log.ipqs_checked = True
        call_log.ipqs_fraud_score = ipqs_result.get('fraud_score', 0) or 0
        call_log.ipqs_is_voip = ipqs_result.get('VOIP', False) or False
        call_log.ipqs_line_type = ipqs_result.get('line_type', '') or ''
        if not ipqs_result.get('success', True) or not ipqs_result.get('valid', True):
            if getattr(campaign, 'block_invalid_numbers', False):
                call_log.ipqs_block_reason = 'invalid_number'
                call_log.status = CallLog.Status.FAILED
                call_log.ended_at = timezone.now()
                call_log.save()
                return JsonResponse({"action": "hangup", "reason": "invalid_number"})
        else:
            should_block, reason = IPQSService.should_block(ipqs_result, campaign)
            if should_block:
                call_log.ipqs_block_reason = reason
                call_log.status = CallLog.Status.FAILED
                call_log.ended_at = timezone.now()
                call_log.save()
                return JsonResponse({"action": "hangup", "reason": reason})
        call_log.save()

    decision = RoutingEngine.route_call(str(campaign.id), {
        'caller_number': caller,
        'twilio_call_sid': call_log.twilio_call_sid,
    })

    if not decision or decision.get('error') or not decision.get('destination'):
        call_log.status = CallLog.Status.FAILED
        call_log.ended_at = timezone.now()
        call_log.save()
        return JsonResponse({
            "action": "hangup",
            "reason": decision.get('error', 'no_destination') if decision else 'no_decision'
        })

    buyer = decision.get('buyer')
    if buyer:
        call_log.buyer = buyer
        call_log.destination_number = buyer.phone_number
    else:
        call_log.destination_number = decision['destination']
    call_log.save()

    return JsonResponse({
        "action": "dial",
        "buyer_number": decision['destination'],
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
    recording_url = data.get('recording_url', '')

    if not call_log_id:
        return JsonResponse({"error": "Missing call_log_id"}, status=400)

    try:
        call_log = CallLog.objects.get(id=call_log_id)
    except CallLog.DoesNotExist:
        return JsonResponse({"error": "Call not found"}, status=404)

    call_log.duration = duration
    call_log.status = CallLog.Status.COMPLETED if answered else CallLog.Status.NO_ANSWER
    call_log.ended_at = timezone.now()
    if answered:
        call_log.answered_at = timezone.now()
    if recording_url:
        call_log.recording_url = recording_url
    call_log.save()

    return JsonResponse({"received": True, "call_log_id": str(call_log.id)})