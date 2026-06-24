from ninja import Router
from django.http import HttpRequest
from typing import List, Optional
from accounts.api import JWTAuth
from .models import RoutingRule, RuleCondition, RuleDestination, CallLog
from .schemas import (
    CreateRoutingRuleSchema, UpdateRoutingRuleSchema,
    RoutingRuleOutSchema, RoutingRuleListSchema,
    CreateConditionSchema, CreateDestinationSchema,
    CallLogOutSchema, CallLogListSchema,
    MessageResponseSchema
)
from .services import RoutingService

router = Router(tags=["Routing"], auth=JWTAuth())


@router.post("/rules", response={201: RoutingRuleOutSchema, 400: dict})
def create_rule(request: HttpRequest, data: CreateRoutingRuleSchema):
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
    try:
        rule = RoutingService.update_rule(rule_id, data, request.auth)
        return 200, RoutingService.format_rule(rule)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.delete("/rules/{rule_id}", response={200: MessageResponseSchema, 404: dict})
def delete_rule(request: HttpRequest, rule_id: str):
    try:
        RoutingService.delete_rule(rule_id, request.auth)
        return 200, {"message": "Rule deleted successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/rules/{rule_id}/conditions", response={201: dict, 400: dict, 404: dict})
def add_condition(request: HttpRequest, rule_id: str, data: CreateConditionSchema):
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
    try:
        destination = RoutingService.add_destination(rule_id, data, request.auth)
        return 201, {
            'id': str(destination.id),
            'destination_type': destination.destination_type,
            'destination': destination.destination,
            'priority': destination.priority,
            'weight': destination.weight,
            'buyer_id': str(destination.buyer_id) if destination.buyer_id else None,
            'buyer_name': destination.buyer.name if destination.buyer else None,
        }
    except ValueError as e:
        return 400, {"detail": str(e)}

@router.get("/calls/live", response={200: List[CallLogListSchema]})
def live_calls(request: HttpRequest):
    calls = CallLog.objects.filter(
        organization=request.auth.organization,
        status=CallLog.Status.IN_PROGRESS
    ).select_related('campaign', 'buyer', 'publisher').order_by('-created_at')

    return 200, [
        {
            'id': str(c.id),
            'caller_number': c.caller_number,
            'called_number': c.called_number,
            'destination_number': c.destination_number,
            'status': c.status,
            'duration': c.duration,
            'campaign_id': str(c.campaign_id) if c.campaign_id else None,
            'campaign_name': c.campaign.name if c.campaign else None,
            'buyer_id': str(c.buyer_id) if c.buyer_id else None,
            'buyer_name': c.buyer.name if c.buyer else None,
            'revenue': str(c.revenue),
            'created_at': c.created_at.isoformat(),
        }
        for c in calls
    ]

@router.get("/calls", response={200: dict})
def list_calls(request: HttpRequest, campaign_id: Optional[str] = None, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    calls = RoutingService.list_calls(request.auth, campaign_id)
    data = [
        {
            'id': str(c.id),
            'caller_number': c.caller_number,
            'called_number': c.called_number,
            'destination_number': c.destination_number,
            'status': c.status,
            'duration': c.duration,
            'campaign_id': str(c.campaign_id) if c.campaign_id else None,
            'campaign_name': c.campaign.name if c.campaign else None,
            'buyer_id': str(c.buyer_id) if c.buyer_id else None,
            'buyer_name': c.buyer.name if c.buyer else None,
            'revenue': str(c.revenue),
            'created_at': c.created_at.isoformat(),
        }
        for c in calls
    ]
    return 200, paginate_list(data, page, page_size)




@router.get("/calls/{call_id}", response={200: CallLogOutSchema, 404: dict})
def get_call(request: HttpRequest, call_id: str):
    try:
        call = RoutingService.get_call(call_id, request.auth)
        return 200, RoutingService.format_call(call)
    except ValueError as e:
        return 404, {"detail": str(e)}

@router.post("/rules/{rule_id}/simulate", response={200: dict, 404: dict})
def simulate_caller(request, rule_id: str):
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
