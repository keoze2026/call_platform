from ninja import Router
from django.http import HttpRequest
from typing import List
from accounts.api import MainAccountAuth
from .schemas import (
    WhiteLabelUpdateSchema, AddDomainSchema,
    WhiteLabelOutSchema, WhiteLabelDomainOutSchema,
    MessageResponseSchema
)
from .services import WhiteLabelService

# Point 3: White Label admin is our main account's only. The public /config
# (a branded site asks for its look, no login) keeps auth=None.
router = Router(tags=["White Label"], auth=MainAccountAuth())


@router.get("/", response={200: WhiteLabelOutSchema})
def get_white_label(request: HttpRequest):
    white_label = WhiteLabelService.get(request.auth)
    return 200, WhiteLabelService.format(white_label)


@router.patch("/", response={200: WhiteLabelOutSchema, 400: dict})
def update_white_label(request: HttpRequest, data: WhiteLabelUpdateSchema):
    try:
        white_label = WhiteLabelService.update(data, request.auth)
        return 200, WhiteLabelService.format(white_label)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/domains", response={201: WhiteLabelDomainOutSchema, 400: dict})
def add_domain(request: HttpRequest, data: AddDomainSchema):
    try:
        domain = WhiteLabelService.add_domain(data, request.auth)
        return 201, {
            'id': str(domain.id),
            'domain': domain.domain,
            'is_primary': domain.is_primary,
            'status': domain.status,
            'verified_at': None,
            'created_at': domain.created_at.isoformat(),
        }
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/domains/{domain_id}/verify", response={200: WhiteLabelDomainOutSchema, 404: dict})
def verify_domain(request: HttpRequest, domain_id: str):
    try:
        domain = WhiteLabelService.verify_domain(domain_id, request.auth)
        return 200, {
            'id': str(domain.id),
            'domain': domain.domain,
            'is_primary': domain.is_primary,
            'status': domain.status,
            'verified_at': domain.verified_at.isoformat() if domain.verified_at else None,
            'created_at': domain.created_at.isoformat(),
        }
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.delete("/domains/{domain_id}", response={200: MessageResponseSchema, 404: dict})
def remove_domain(request: HttpRequest, domain_id: str):
    try:
        WhiteLabelService.remove_domain(domain_id, request.auth)
        return 200, {"message": "Domain removed successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.get("/config", auth=None, response={200: dict, 404: dict})
def get_config_by_domain(request: HttpRequest):
    host = request.META.get('HTTP_HOST', '')
    white_label = WhiteLabelService.get_by_domain(host)
    if not white_label:
        return 404, {"detail": "No white label config found for this domain"}
    return 200, WhiteLabelService.format(white_label)