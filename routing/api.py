from accounts.permissions import require, Capability
from accounts.permissions import scope_queryset
from ninja import Router
from django.http import HttpRequest
from typing import List, Optional
from accounts.api import JWTAuth
from .models import RoutingRule, RuleCondition, RuleDestination, CallLog
from .schemas import (
    CreateRoutingRuleSchema, UpdateRoutingRuleSchema,
    RoutingRuleOutSchema, RoutingRuleListSchema,
    CreateConditionSchema, CreateDestinationSchema,
    UpdateDestinationSchema,
    CallLogOutSchema, CallLogListSchema,
    MessageResponseSchema
)
from .services import RoutingService

router = Router(tags=["Routing"], auth=JWTAuth())


@router.post("/rules", response={201: RoutingRuleOutSchema, 400: dict})
def create_rule(request: HttpRequest, data: CreateRoutingRuleSchema):
    require(request.auth, Capability.CREATE)
    try:
        rule = RoutingService.create_rule(data, request.auth)
        return 201, RoutingService.format_rule(rule)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/rules", response={200: dict})
def list_rules(request: HttpRequest, campaign_id: Optional[str] = None, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    rules = RoutingService.list_rules(request.auth, campaign_id)
    data = [
        {
            'id': str(r.id),
            'name': r.name,
            'rule_type': r.rule_type,
            'priority': r.priority,
            'status': r.status,
            'campaign_id': str(r.campaign_id),
            'campaign_name': r.campaign.name,
            'destination_count': r.destination_count,
            'condition_count': r.condition_count,
            'created_at': r.created_at.isoformat(),
        }
        for r in rules
    ]
    return 200, paginate_list(data, page, page_size)


@router.get("/rules/{rule_id}", response={200: RoutingRuleOutSchema, 404: dict})
def get_rule(request: HttpRequest, rule_id: str):
    try:
        rule = RoutingService.get_rule(rule_id, request.auth)
        return 200, RoutingService.format_rule(rule)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/rules/{rule_id}", response={200: RoutingRuleOutSchema, 400: dict, 404: dict})
def update_rule(request: HttpRequest, rule_id: str, data: UpdateRoutingRuleSchema):
    require(request.auth, Capability.EDIT)
    try:
        rule = RoutingService.update_rule(rule_id, data, request.auth)
        return 200, RoutingService.format_rule(rule)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.delete("/rules/{rule_id}", response={200: MessageResponseSchema, 404: dict})
def delete_rule(request: HttpRequest, rule_id: str):
    require(request.auth, Capability.DELETE)
    try:
        RoutingService.delete_rule(rule_id, request.auth)
        return 200, {"message": "Rule deleted successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/rules/{rule_id}/conditions", response={201: dict, 400: dict, 404: dict})
def add_condition(request: HttpRequest, rule_id: str, data: CreateConditionSchema):
    require(request.auth, Capability.CREATE)
    try:
        condition = RoutingService.add_condition(rule_id, data, request.auth)
        return 201, {
            'id': str(condition.id),
            'conditions': condition.conditions,
        }
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/rules/{rule_id}/destinations", response={201: dict, 400: dict, 404: dict})
def add_destination(request: HttpRequest, rule_id: str, data: CreateDestinationSchema):
    require(request.auth, Capability.CREATE)
    try:
        destination = RoutingService.add_destination(rule_id, data, request.auth)
        return 201, RoutingService.format_destination(destination)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/rules/{rule_id}/destinations", response={200: List[dict], 404: dict})
def list_destinations(request: HttpRequest, rule_id: str):
    try:
        destinations = RoutingService.list_destinations(rule_id, request.auth)
        return 200, [RoutingService.format_destination(d) for d in destinations]
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/rules/{rule_id}/destinations/{destination_id}", response={200: dict, 400: dict, 404: dict})
def update_destination(request: HttpRequest, rule_id: str, destination_id: str, data: UpdateDestinationSchema):
    require(request.auth, Capability.EDIT)
    import json as _json
    try:
        body = _json.loads(request.body)
    except Exception:
        body = {}
    # buyer_id explicitly sent as null means detach the buyer
    data._detach_buyer = 'buyer_id' in body and body['buyer_id'] is None

    try:
        destination = RoutingService.update_destination(rule_id, destination_id, data, request.auth)
        return 200, RoutingService.format_destination(destination)
    except ValueError as e:
        if 'not found' in str(e).lower():
            return 404, {"detail": str(e)}
        return 400, {"detail": str(e)}


@router.delete("/rules/{rule_id}/destinations/{destination_id}", response={200: MessageResponseSchema, 404: dict})
def delete_destination(request: HttpRequest, rule_id: str, destination_id: str):
    require(request.auth, Capability.DELETE)
    try:
        RoutingService.delete_destination(rule_id, destination_id, request.auth)
        return 200, {"message": "Destination deleted successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}

@router.get("/calls/live", response={200: List[CallLogListSchema]})
def live_calls(request: HttpRequest):
    calls = scope_queryset(request.auth, CallLog.objects.filter(
        organization=request.auth.organization,
        status=CallLog.Status.IN_PROGRESS
    ).select_related('campaign', 'buyer', 'publisher')).order_by('-created_at')

    return 200, [RoutingService.format_call(c) for c in calls]

@router.get("/calls", response={200: dict})
def list_calls(request: HttpRequest, campaign_id: Optional[str] = None, status: Optional[str] = None, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    calls = RoutingService.list_calls(request.auth, campaign_id, status)
    data = [RoutingService.format_call(c) for c in calls]
    return 200, paginate_list(data, page, page_size)




@router.get("/calls/{call_id}", response={200: CallLogOutSchema, 404: dict})
def get_call(request: HttpRequest, call_id: str):
    try:
        call = RoutingService.get_call(call_id, request.auth)
        return 200, RoutingService.format_call(call)
    except ValueError as e:
        return 404, {"detail": str(e)}

@router.post("/calls/{call_id}/hangup", response={200: dict, 400: dict, 404: dict})
def hangup_call(request: HttpRequest, call_id: str):
    """End a call that is showing as live.

    Closes the record: sets a terminal status, stamps ended_at, and derives the
    duration from answered_at when the call had connected. The analytics mirror
    follows through the usual post_save signal, and billing runs the same path
    call_ended uses - idempotent on call_sid, so a webhook arriving afterwards
    cannot charge the call twice.

    It does NOT drop live audio. Asterisk owns the channel and there is no AMI
    connection from here, so a genuinely connected call keeps talking until the
    parties hang up. Its purpose is clearing rows stuck as live because no
    end-of-call webhook arrived.
    """
    require(request.auth, Capability.CREATE)
    from django.conf import settings
    from django.utils import timezone

    try:
        call_log = scope_queryset(
            request.auth,
            CallLog.objects.select_related('campaign', 'buyer', 'publisher'),
        ).get(id=call_id, organization=request.auth.organization)
    except CallLog.DoesNotExist:
        return 404, {"detail": "Call not found"}

    if call_log.status not in (CallLog.Status.RINGING, CallLog.Status.IN_PROGRESS):
        return 400, {
            "detail": f"Call is already {call_log.status}, nothing to hang up",
            "status": call_log.status,
        }

    now = timezone.now()

    # A call that connected has a real duration to bill; one that never answered
    # has none, and must not be charged.
    if call_log.answered_at:
        duration = max(int((now - call_log.answered_at).total_seconds()), 0)
        status = CallLog.Status.COMPLETED
    else:
        duration = 0
        status = CallLog.Status.NO_ANSWER

    call_log.status = status
    call_log.duration = duration
    call_log.ended_at = now
    call_log.block_reason = 'manual_hangup'

    campaign = call_log.campaign
    min_dur = getattr(campaign, 'min_call_duration', 0) if campaign else 0
    converted = status == CallLog.Status.COMPLETED and duration >= min_dur

    call_log.revenue = (getattr(campaign, 'revenue_amount', 0) or 0) if converted else 0
    call_log.publisher_payout = (
        RoutingEngine.required_call_balance(campaign, None) if converted else 0
    )
    call_log.save()

    charged = None
    if converted and getattr(settings, 'CHARGE_COMPLETED_CALLS', True):
        try:
            from billing.services import BillingService
            from phone_numbers.models import PhoneNumber

            phone = PhoneNumber.objects.filter(number=call_log.called_number).first()
            amount = BillingService.call_cost(call_log.organization, duration)
            if amount > 0:
                tx = BillingService.charge_call(
                    organization=call_log.organization,
                    campaign=campaign,
                    buyer=call_log.buyer,
                    publisher=call_log.publisher,
                    amount=amount,
                    call_sid=call_log.twilio_call_sid,
                )
                charged = str(amount) if tx else None
        except Exception:
            pass

    return 200, {
        "id": str(call_log.id),
        "status": call_log.status,
        "duration": duration,
        "converted": converted,
        "charged": charged,
        "ended_at": now.isoformat(),
        "message": "Call record closed. Live audio, if any, is not affected.",
    }


@router.post("/rules/{rule_id}/simulate", response={200: dict, 404: dict})
def simulate_caller(request, rule_id: str):
    require(request.auth, Capability.CREATE)
    import json as _json
    from routing.models import RoutingRule
    try:
        body = _json.loads(request.body)
        caller_number = body.get('caller_number', '')
        caller_state = body.get('caller_state', '')
        caller_country = body.get('caller_country', '')

        rule = RoutingRule.objects.get(id=rule_id, campaign__organization=request.auth.organization)

        trace = []
        matched_conditions = []

        # Check conditions
        conditions = rule.conditions.all()
        all_matched = True
        for cond in conditions:
            matched = False
            cond_data = cond.conditions or {}
            if 'state' in cond_data:
                matched = caller_state in cond_data['state']
            elif 'country' in cond_data:
                matched = caller_country in cond_data['country']
            else:
                matched = True
            matched_conditions.append({'condition_id': str(cond.id), 'matched': matched})
            if not matched:
                all_matched = False
            trace.append({'step': f'Condition {cond.id}', 'outcome': 'matched' if matched else 'not matched'})

        # Select destination
        selected_destination = None
        if all_matched or not conditions.exists():
            destinations = rule.destinations.all().order_by('priority', '-weight')
            if destinations.exists():
                dest = destinations.first()
                selected_destination = {
                    'id': str(dest.id),
                    'name': dest.destination,
                    'buyer_name': dest.buyer.name if dest.buyer else '',
                    'weight': dest.weight,
                    'priority': dest.priority,
                }
                trace.append({'step': 'Destination selected', 'outcome': dest.destination})
            else:
                trace.append({'step': 'Destination selection', 'outcome': 'no destinations configured'})
        else:
            trace.append({'step': 'Routing', 'outcome': 'conditions not met, call not routed'})

        return 200, {
            'matched_conditions': matched_conditions,
            'selected_destination': selected_destination,
            'trace': trace,
        }
    except RoutingRule.DoesNotExist:
        return 404, {"detail": "Rule not found"}
