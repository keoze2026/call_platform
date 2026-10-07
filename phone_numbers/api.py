from accounts.permissions import require, Capability
from ninja import Router
from django.http import HttpRequest
from typing import List
from .schemas import (
    SearchNumberSchema, PurchaseNumberSchema,
    AssignNumberSchema, UpdateNumberSchema,
    AvailableNumberSchema, PhoneNumberOutSchema,
    PhoneNumberListSchema, MessageResponseSchema,
    CarrierInSchema, CarrierUpdateSchema, CarrierOutSchema,
)
from .services import PhoneNumberService
from accounts.api import JWTAuth

router = Router(tags=["Phone Numbers"], auth=JWTAuth())
carriers_router = Router(tags=["Carriers"], auth=JWTAuth())


@router.post("/search", response={200: List[AvailableNumberSchema], 400: dict})
def search_numbers(request: HttpRequest, data: SearchNumberSchema):
    require(request.auth, Capability.CREATE)
    try:
        numbers = PhoneNumberService.search_available_numbers(data, request.auth)
        return 200, numbers
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/purchase", response={201: PhoneNumberOutSchema, 400: dict})
def purchase_number(request: HttpRequest, data: PurchaseNumberSchema):
    require(request.auth, Capability.CREATE)
    try:
        phone_number = PhoneNumberService.purchase_number(data, request.auth)
        trunk_warning = getattr(phone_number, 'trunk_warning', None)
        phone_number = PhoneNumberService.get_number(str(phone_number.id), request.auth)
        # get_number refetches from the DB, so carry the warning across
        phone_number.trunk_warning = trunk_warning
        return 201, PhoneNumberService.format_number(phone_number)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/import", response={201: PhoneNumberOutSchema, 400: dict})
def import_number(request: HttpRequest, data: PurchaseNumberSchema):
    require(request.auth, Capability.CREATE)
    try:
        phone_number = PhoneNumberService.import_existing_number(data, request.auth)
        phone_number = PhoneNumberService.get_number(str(phone_number.id), request.auth)
        return 201, PhoneNumberService.format_number(phone_number)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("", response={200: dict})
def list_numbers(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    from routing.models import CallLog
    from django.db.models import Count
    
    numbers = PhoneNumberService.list_numbers(request.auth)
    
    # Pre-calculate live calls for the organization to prevent N+1 queries
    live_calls_qs = CallLog.objects.filter(
        organization=request.auth.organization,
        status__in=['in_progress', 'ringing', 'initiated']
    ).values('called_number').annotate(count=Count('id'))
    live_calls_map = {item['called_number']: item['count'] for item in live_calls_qs}

    data = [PhoneNumberService.format_number(n, live_calls_count=live_calls_map.get(n.number, 0)) for n in numbers]
    return 200, paginate_list(data, page, page_size)


@router.get("/{number_id}", response={200: PhoneNumberOutSchema, 404: dict})
def get_number(request: HttpRequest, number_id: str):
    try:
        phone_number = PhoneNumberService.get_number(number_id, request.auth)
        return 200, PhoneNumberService.format_number(phone_number)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/{number_id}", response={200: PhoneNumberOutSchema, 400: dict, 404: dict})
def update_number(request: HttpRequest, number_id: str, data: UpdateNumberSchema):
    require(request.auth, Capability.EDIT)
    try:
        import json as _json
        try:
            body = _json.loads(request.body)
        except Exception:
            body = {}
        # If campaign_id explicitly sent as null, detach
        if 'campaign_id' in body and body['campaign_id'] is None:
            data._detach_campaign = True
        else:
            data._detach_campaign = False
        phone_number = PhoneNumberService.update_number(number_id, data, request.auth)
        return 200, PhoneNumberService.format_number(phone_number)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{number_id}/assign", response={200: PhoneNumberOutSchema, 400: dict, 404: dict})
def assign_number(request: HttpRequest, number_id: str, data: AssignNumberSchema):
    require(request.auth, Capability.CREATE)
    try:
        phone_number = PhoneNumberService.assign_number(number_id, data, request.auth)
        return 200, PhoneNumberService.format_number(phone_number)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/{number_id}/release", response={200: MessageResponseSchema, 400: dict, 404: dict})
def release_number(request: HttpRequest, number_id: str):
    require(request.auth, Capability.DELETE)
    try:
        PhoneNumberService.release_number(number_id, request.auth)
        return 200, {"message": "Number released successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


# ── Carriers ─────────────────────────────────────────────────────────────────
# Which carrier actually carries a number, kept as rows rather than choices in
# the code so a new one can be added the day it is signed, without a deploy.
# Routed before /{number_id} would match "carriers" as an id, so these are
# declared on their own prefix.

def _carrier_out(c) -> dict:
    return {
        'id': str(c.id),
        'name': c.name,
        'code': c.code,
        'is_active': c.is_active,
        'notes': c.notes,
        'numbers_count': c.phone_numbers.count(),
        'created_at': c.created_at.isoformat(),
    }


@carriers_router.get("", response={200: List[CarrierOutSchema]})
def list_carriers(request: HttpRequest):
    from .models import Carrier
    rows = Carrier.objects.filter(organization=request.auth.organization)
    return 200, [_carrier_out(c) for c in rows]


@carriers_router.post("", response={201: CarrierOutSchema, 400: dict})
def create_carrier(request: HttpRequest, data: CarrierInSchema):
    require(request.auth, Capability.CREATE)
    from django.db import IntegrityError
    from .models import Carrier
    name = (data.name or '').strip()
    code = (data.code or '').strip().upper()
    if not name or not code:
        return 400, {"detail": "A carrier needs both a name and a code."}
    try:
        c = Carrier.objects.create(
            organization=request.auth.organization,
            name=name, code=code,
            is_active=True if data.is_active is None else data.is_active,
            notes=data.notes or '',
        )
    except IntegrityError:
        return 400, {"detail": f"The code {code} is already used by another carrier."}
    return 201, _carrier_out(c)


@carriers_router.patch("/{carrier_id}", response={200: CarrierOutSchema, 400: dict, 404: dict})
def update_carrier(request: HttpRequest, carrier_id: str, data: CarrierUpdateSchema):
    require(request.auth, Capability.UPDATE)
    from django.db import IntegrityError
    from .models import Carrier
    try:
        c = Carrier.objects.get(id=carrier_id, organization=request.auth.organization)
    except Carrier.DoesNotExist:
        return 404, {"detail": "Carrier not found"}
    if data.name is not None:
        c.name = data.name.strip()
    if data.code is not None:
        c.code = data.code.strip().upper()
    if data.is_active is not None:
        c.is_active = data.is_active
    if data.notes is not None:
        c.notes = data.notes
    try:
        c.save()
    except IntegrityError:
        return 400, {"detail": f"The code {c.code} is already used by another carrier."}
    return 200, _carrier_out(c)


@carriers_router.delete("/{carrier_id}", response={200: MessageResponseSchema, 400: dict, 404: dict})
def delete_carrier(request: HttpRequest, carrier_id: str):
    require(request.auth, Capability.DELETE)
    from .models import Carrier
    try:
        c = Carrier.objects.get(id=carrier_id, organization=request.auth.organization)
    except Carrier.DoesNotExist:
        return 404, {"detail": "Carrier not found"}
    # Numbers keep working; they simply stop naming a carrier. Deactivating is
    # the gentler option and the one the UI should offer first.
    n = c.phone_numbers.count()
    c.delete()
    return 200, {"message": f"Carrier removed from {n} number(s)."}
