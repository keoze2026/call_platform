from ninja import Router
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from typing import List
from accounts.api import JWTAuth
from .schemas import (
    CreateIVRFlowSchema, UpdateIVRFlowSchema,
    CreateIVRNodeSchema, CreateIVRTransitionSchema,
    IVRFlowOutSchema, IVRFlowListSchema,
    MessageResponseSchema
)
from .services import IVRService

router = Router(tags=["IVR"], auth=JWTAuth())


@router.post("/flows", response={201: IVRFlowOutSchema, 400: dict})
def create_flow(request: HttpRequest, data: CreateIVRFlowSchema):
    try:
        flow = IVRService.create_flow(data, request.auth)
        flow = IVRService.get_flow(str(flow.id), request.auth)
        return 201, IVRService.format_flow(flow)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/flows", response={200: dict})
def list_flows(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    flows = IVRService.list_flows(request.auth)
    data = [
        {
            'id': str(f.id),
            'name': f.name,
            'status': f.status,
            'campaign_id': str(f.campaign_id) if f.campaign_id else None,
            'campaign_name': f.campaign.name if f.campaign else None,
            'created_at': f.created_at.isoformat(),
        }
        for f in flows
    ]
    return 200, paginate_list(data, page, page_size)


@router.get("/flows/{flow_id}", response={200: IVRFlowOutSchema, 404: dict})
def get_flow(request: HttpRequest, flow_id: str):
    try:
        flow = IVRService.get_flow(flow_id, request.auth)
        return 200, IVRService.format_flow(flow)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/flows/{flow_id}", response={200: IVRFlowOutSchema, 400: dict, 404: dict})
def update_flow(request: HttpRequest, flow_id: str, data: UpdateIVRFlowSchema):
    try:
        IVRService.update_flow(flow_id, data, request.auth)
        flow = IVRService.get_flow(flow_id, request.auth)
        return 200, IVRService.format_flow(flow)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/flows/{flow_id}", response={200: MessageResponseSchema, 404: dict})
def delete_flow(request: HttpRequest, flow_id: str):
    try:
        IVRService.delete_flow(flow_id, request.auth)
        return 200, {"message": "IVR flow deleted successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/flows/{flow_id}/nodes", response={201: dict, 400: dict, 404: dict})
def add_node(request: HttpRequest, flow_id: str, data: CreateIVRNodeSchema):
    try:
        node = IVRService.add_node(flow_id, data, request.auth)
        return 201, {
            'id': str(node.id),
            'node_type': node.node_type,
            'name': node.name,
            'position': node.position,
            'is_entry_point': node.is_entry_point,
            'config': node.config,
        }
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/flows/{flow_id}/transitions", response={201: dict, 400: dict, 404: dict})
def add_transition(request: HttpRequest, flow_id: str, data: CreateIVRTransitionSchema):
    try:
        transition = IVRService.add_transition(flow_id, data, request.auth)
        return 201, {
            'id': str(transition.id),
            'trigger': transition.trigger,
            'from_node_id': str(transition.from_node_id),
            'to_node_id': str(transition.to_node_id) if transition.to_node_id else None,
        }
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/webhook/{flow_id}/", auth=None, response=None)
def ivr_webhook(request: HttpRequest, flow_id: str):
    twiml = IVRService.execute_flow(flow_id)
    return HttpResponse(twiml, content_type='text/xml')


@router.post("/webhook/gather/{flow_id}/{node_id}/", auth=None, response=None)
def ivr_gather_webhook(request: HttpRequest, flow_id: str, node_id: str):
    digit = request.POST.get('Digits', '')
    twiml = IVRService.execute_flow(flow_id, digit=digit, node_id=node_id)
    return HttpResponse(twiml, content_type='text/xml')