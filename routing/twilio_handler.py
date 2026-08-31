from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from twilio.twiml.voice_response import VoiceResponse, Dial
from twilio.request_validator import RequestValidator
from django.conf import settings
from .models import CallLog, RoutingRule
from .engine import RoutingEngine
from phone_numbers.models import PhoneNumber


def validate_twilio_request(request: HttpRequest) -> bool:
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    signature = request.headers.get('X-Twilio-Signature', '')
    url = request.build_absolute_uri()
    post_data = request.POST.dict()
    return validator.validate(url, post_data, signature)


@csrf_exempt
def incoming_call(request: HttpRequest) -> HttpResponse:
    if request.method != 'POST':
        return HttpResponse(status=405)

    call_sid = request.POST.get('CallSid', '')
    caller_number = request.POST.get('From', '')
    called_number = request.POST.get('To', '')
    caller_state = request.POST.get('FromState', '')
    caller_country = request.POST.get('FromCountry', '')

    caller_area_code = ''
    if caller_number and len(caller_number) >= 5:
        cleaned = caller_number.replace('+1', '').replace('+', '')
        caller_area_code = cleaned[:3]

    if not caller_state and caller_area_code:
        caller_state = RoutingEngine.get_caller_state(caller_area_code)

    response = VoiceResponse()

    try:
        phone_number = PhoneNumber.objects.select_related(
            'campaign', 'publisher'
        ).get(number=called_number, status='active')

        if not phone_number.campaign:
            response.say("This number is not configured. Please try again later.")
            response.hangup()
            return HttpResponse(str(response), content_type='text/xml')

        campaign = phone_number.campaign

        # Play greeting message to caller before routing
        if campaign.greeting_enabled and campaign.greeting_message:
            response.say(campaign.greeting_message)

        call_data = {
            'caller_number': caller_number,
            'caller_area_code': caller_area_code,
            'caller_state': caller_state,
            'caller_country': caller_country,
            'call_time': timezone.now(),
            'tags': [],
            'twilio_call_sid': call_sid,
        }

        routing_result = RoutingEngine.route_call(str(campaign.id), call_data)

        call_log = CallLog.objects.create(
            organization=campaign.organization,
            campaign=campaign,
            routing_rule=routing_result.get('rule'),
            buyer=routing_result.get('buyer'),
            publisher=phone_number.publisher,
            caller_number=caller_number,
            called_number=called_number,
            twilio_call_sid=call_sid,
            status=CallLog.Status.RINGING,
            caller_area_code=caller_area_code,
            caller_state=caller_state,
            caller_country=caller_country,
            buyer_payout=campaign.payout_amount,
            revenue=campaign.revenue_amount,
            publisher_payout=phone_number.publisher.payout_amount if phone_number.publisher else 0,
        )

        # Broadcast new call to dashboard
        from routing.broadcast import broadcast_call_to_org
        broadcast_call_to_org(str(campaign.organization_id), {
            'event': 'call_started',
            'call_id': str(call_log.id),
            'caller_number': caller_number,
            'called_number': called_number,
            'campaign_name': campaign.name,
            'status': 'ringing',
        })

        destination = routing_result.get('destination')

        if destination:
            call_log.destination_number = destination
            call_log.save(update_fields=['destination_number'])

            action_url = f"{settings.BASE_URL}/api/twilio/call-status/"

            dial = Dial(
                action=action_url,
                method='POST',
                timeout=30,
                record='record-from-answer' if campaign.recording_enabled else 'do-not-record',
            )

            # Add whisper message to buyer before connecting
            if campaign.whisper_enabled and campaign.whisper_message:
                from twilio.twiml.voice_response import Number
                number = Number(destination)
                number.url = f"{settings.BASE_URL}/api/twilio/whisper/{str(campaign.id)}/"
                dial.append(number)
            else:
                dial.number(destination)

            response.append(dial)
        else:
            # No buyer available — check if queuing is enabled
            if campaign.queue_enabled:
                from call_queue.services import CallQueueService

                if CallQueueService.check_queue_full(campaign):
                    response.say("All agents are busy and the queue is full. Please try again later.")
                    response.hangup()
                    call_log.status = CallLog.Status.NO_ANSWER
                    call_log.save(update_fields=['status'])
                else:
                    entry = CallQueueService.enqueue(
                        campaign=campaign,
                        caller_number=caller_number,
                        called_number=called_number,
                        twilio_call_sid=call_sid,
                    )
                    twiml = CallQueueService.get_queue_twiml(campaign, entry)
                    return HttpResponse(twiml, content_type='text/xml')
            else:
                response.say("We are sorry, no agents are available right now. Please try again later.")

                # Auto SMS reply when no answer
                if campaign.auto_sms_enabled and campaign.auto_sms_message:
                    try:
                        from twilio.rest import Client
                        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
                        client.messages.create(
                            body=campaign.auto_sms_message,
                            from_=called_number,
                            to=caller_number
                        )
                    except Exception:
                        pass

                response.hangup()

                call_log.status = CallLog.Status.NO_ANSWER
                call_log.save(update_fields=['status'])

                
                # Broadcast call update to dashboard
                from routing.broadcast import broadcast_call_to_org
                broadcast_call_to_org(str(call_log.organization_id), {
                    'event': 'call_updated',
                    'call_id': str(call_log.id),
                    'status': call_log.status,
                    'duration': call_log.duration,
                })

    except PhoneNumber.DoesNotExist:
        response.say("This number is not recognized. Please try again later.")
        response.hangup()

    except Exception as e:
        response.say("We are experiencing technical difficulties. Please try again later.")
        response.hangup()

    return HttpResponse(str(response), content_type='text/xml')


@csrf_exempt
def whisper(request: HttpRequest, campaign_id: str) -> HttpResponse:
    """Plays whisper message to buyer before connecting"""
    response = VoiceResponse()

    try:
        from campaigns.models import Campaign
        campaign = Campaign.objects.get(id=campaign_id)
        if campaign.whisper_message:
            response.say(campaign.whisper_message)
    except Exception:
        pass

    return HttpResponse(str(response), content_type='text/xml')


@csrf_exempt
def call_status(request: HttpRequest) -> HttpResponse:
    if request.method != 'POST':
        return HttpResponse(status=405)

    if not validate_twilio_request(request):
        return HttpResponse(status=403)

    call_sid = request.POST.get('CallSid', '')
    twilio_status = request.POST.get('CallStatus', '')
    call_duration = request.POST.get('CallDuration', 0)
    recording_url = request.POST.get('RecordingUrl', '')
    recording_sid = request.POST.get('RecordingSid', '')

    status_map = {
        'completed': CallLog.Status.COMPLETED,
        'no-answer': CallLog.Status.NO_ANSWER,
        'busy': CallLog.Status.BUSY,
        'failed': CallLog.Status.FAILED,
        'in-progress': CallLog.Status.IN_PROGRESS,
    }

    # Twilio sends DialCallStatus when <Dial> action fires
    dial_status = request.POST.get('DialCallStatus', '')

    # If dial failed (busy/no-answer/failed), try next destination
    if dial_status in ('busy', 'no-answer', 'failed'):
        try:
            failed_log = CallLog.objects.get(twilio_call_sid=call_sid)
            from routing.engine import RoutingEngine

            call_data = {
                'caller_number': failed_log.caller_number,
                'caller_area_code': failed_log.caller_area_code,
                'caller_state': failed_log.caller_state,
                'call_time': timezone.now(),
                'tags': list(failed_log.tags) if failed_log.tags else [],
                'twilio_call_sid': call_sid,
                'exclude_buyer_id': str(failed_log.buyer_id) if failed_log.buyer_id else None,
            }

            routing_result = RoutingEngine.route_call(str(failed_log.campaign_id), call_data)
            next_destination = routing_result.get('destination')

            if next_destination:
                response = VoiceResponse()
                action_url = f"{settings.BASE_URL}/api/twilio/call-status/"
                dial = Dial(action=action_url, method='POST', timeout=30)
                dial.number(next_destination)
                response.append(dial)

                failed_log.buyer = routing_result.get('buyer')
                failed_log.destination_number = next_destination
                failed_log.save(update_fields=['buyer', 'destination_number'])

                return HttpResponse(str(response), content_type='text/xml')
        except CallLog.DoesNotExist:
            pass

    try:
        call_log = CallLog.objects.get(twilio_call_sid=call_sid)

        call_log.status = status_map.get(twilio_status, CallLog.Status.FAILED) 
        call_log.duration = int(call_duration) if call_duration else 0
        call_log.ended_at = timezone.now()

        if recording_url:
            call_log.recording_url = recording_url
            call_log.recording_sid = recording_sid

        call_log.save()

        # Kick off transcription in the background (only if recording exists)
        if call_log.recording_url and call_log.status == CallLog.Status.COMPLETED:
            try:
                from tasks import transcribe_call_recording
                transcribe_call_recording.delay(str(call_log.id))
            except Exception:
                pass

        # Update buyer quality score on terminal statuses
        if call_log.buyer and call_log.status in (CallLog.Status.COMPLETED, CallLog.Status.NO_ANSWER, CallLog.Status.BUSY, CallLog.Status.FAILED):
            try:
                from rtb.services import RTBService
                RTBService.update_buyer_quality_score(call_log.buyer)
            except Exception:
                pass
            
        # Charge organization for completed call
        if call_log.status == CallLog.Status.COMPLETED and call_log.duration > 0:
            try:
                from billing.services import BillingService
                campaign = call_log.campaign
                if campaign and campaign.revenue_amount > 0:
                    BillingService.charge_call(
                        organization=call_log.organization,
                        campaign=campaign,
                        buyer=call_log.buyer,
                        publisher=call_log.publisher,
                        amount=campaign.revenue_amount,
                        call_sid=call_sid,
                    )
                if call_log.publisher and call_log.publisher.payout_amount > 0:
                    BillingService.payout(
                        organization=call_log.organization,
                        publisher=call_log.publisher,
                        amount=call_log.publisher.payout_amount,
                        call_sid=call_sid,
                    )
            except Exception:
                pass

        # Auto SMS on missed call
        if call_log.status == CallLog.Status.NO_ANSWER:
            try:
                campaign = call_log.campaign
                if campaign and campaign.auto_sms_enabled and campaign.auto_sms_message:
                    from twilio.rest import Client
                    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
                    client.messages.create(
                        body=campaign.auto_sms_message,
                        from_=call_log.called_number,
                        to=call_log.caller_number
                    )
            except Exception:
                pass

        # Fire notifications
        from notifications.services import NotificationService

        if call_log.status == CallLog.Status.NO_ANSWER:
            NotificationService.dispatch('call.missed', call_log.organization, {
                'caller_number': call_log.caller_number,
                'campaign_name': call_log.campaign.name if call_log.campaign else '',
                'called_number': call_log.called_number,
            })

        elif call_log.status == CallLog.Status.COMPLETED:
            NotificationService.dispatch('call.completed', call_log.organization, {
                'caller_number': call_log.caller_number,
                'campaign_name': call_log.campaign.name if call_log.campaign else '',
                'duration': call_log.duration,
            })

        # Fire webhooks
        from webhooks.services import WebhookService
        event_map = {
            CallLog.Status.COMPLETED: 'call.completed',
            CallLog.Status.NO_ANSWER: 'call.no_answer',
            CallLog.Status.FAILED: 'call.failed',
        }
        event = event_map.get(call_log.status)
        if event:
            WebhookService.dispatch(str(call_log.organization_id), event, {
                'call_sid': call_sid,
                'caller_number': call_log.caller_number,
                'called_number': call_log.called_number,
                'status': call_log.status,
                'duration': call_log.duration,
                'recording_url': call_log.recording_url,
                'campaign_id': str(call_log.campaign_id) if call_log.campaign_id else None,
            })

        # CRM webhook — fires on every completed call
        if call_log.status == CallLog.Status.COMPLETED:
            from webhooks.services import WebhookService
            WebhookService.dispatch(str(call_log.organization_id), 'crm.contact_created', {
                'call_sid': call_sid,
                'caller_number': call_log.caller_number,
                'called_number': call_log.called_number,
                'campaign_id': str(call_log.campaign_id) if call_log.campaign_id else None,
                'campaign_name': call_log.campaign.name if call_log.campaign else '',
                'buyer_id': str(call_log.buyer_id) if call_log.buyer_id else None,
                'buyer_name': call_log.buyer.name if call_log.buyer else '',
                'duration': call_log.duration,
                'recording_url': call_log.recording_url or '',
                'started_at': call_log.created_at.isoformat(),
                'ended_at': call_log.ended_at.isoformat() if call_log.ended_at else '',
            })

    except CallLog.DoesNotExist:
        pass

    return HttpResponse('', content_type='text/xml')

@csrf_exempt
def click_to_call(request: HttpRequest) -> HttpResponse:
    """
    Public endpoint — website widget calls this to initiate a click-to-call.
    Expects: campaign_id, visitor_number (the person who clicked).
    """
    import json
    from twilio.rest import Client
    from campaigns.models import Campaign

    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        data = request.POST.dict()

    campaign_id = data.get('campaign_id', '')
    visitor_number = data.get('visitor_number', '')

    if not campaign_id or not visitor_number:
        return HttpResponse(
            json.dumps({'success': False, 'error': 'campaign_id and visitor_number required'}),
            content_type='application/json',
            status=400,
        )

    try:
        campaign = Campaign.objects.get(id=campaign_id, status='active')
    except Campaign.DoesNotExist:
        return HttpResponse(
            json.dumps({'success': False, 'error': 'Campaign not found or inactive'}),
            content_type='application/json',
            status=404,
        )

    # Find a tracking number for this campaign to call FROM
    from phone_numbers.models import PhoneNumber
    phone_number = PhoneNumber.objects.filter(campaign=campaign, status='active').first()
    if not phone_number:
        return HttpResponse(
            json.dumps({'success': False, 'error': 'No number configured for this campaign'}),
            content_type='application/json',
            status=400,
        )

    try:
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        # Call the visitor first; when they answer, Twilio hits our incoming-call logic
        call = client.calls.create(
            to=visitor_number,
            from_=phone_number.number,
            url=f"{settings.BASE_URL}/api/twilio/click-to-call-connect/{campaign_id}/",
        )
        return HttpResponse(
            json.dumps({'success': True, 'call_sid': call.sid}),
            content_type='application/json',
        )
    except Exception as e:
        return HttpResponse(
            json.dumps({'success': False, 'error': str(e)}),
            content_type='application/json',
            status=500,
        )


@csrf_exempt
def click_to_call_connect(request: HttpRequest, campaign_id: str) -> HttpResponse:
    """
    When the visitor answers, this routes them to a buyer via the normal engine.
    """
    from campaigns.models import Campaign

    response = VoiceResponse()

    try:
        campaign = Campaign.objects.get(id=campaign_id)
    except Campaign.DoesNotExist:
        response.say("This campaign is no longer available.")
        response.hangup()
        return HttpResponse(str(response), content_type='text/xml')

    if campaign.greeting_enabled and campaign.greeting_message:
        response.say(campaign.greeting_message)

    call_data = {
        'caller_number': request.POST.get('To', ''),
        'caller_area_code': '',
        'caller_state': '',
        'call_time': timezone.now(),
        'tags': ['click-to-call'],
        'twilio_call_sid': request.POST.get('CallSid', ''),
    }

    routing_result = RoutingEngine.route_call(str(campaign.id), call_data)
    destination = routing_result.get('destination')

    if destination:
        action_url = f"{settings.BASE_URL}/api/twilio/call-status/"
        dial = Dial(
            action=action_url,
            method='POST',
            timeout=30,
            record='record-from-answer' if campaign.recording_enabled else 'do-not-record',
        )
        dial.number(destination)
        response.append(dial)
    else:
        response.say("We are sorry, no agents are available right now.")
        response.hangup()

    return HttpResponse(str(response), content_type='text/xml')