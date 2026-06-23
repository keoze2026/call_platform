from ninja import Router
from django.http import HttpRequest
from typing import List
from .schemas import (
    SearchNumberSchema, PurchaseNumberSchema,
    AssignNumberSchema, UpdateNumberSchema,
    AvailableNumberSchema, PhoneNumberOutSchema,
    PhoneNumberListSchema, MessageResponseSchema
)
from .services import PhoneNumberService
from accounts.api import JWTAuth

router = Router(tags=["Phone Numbers"], auth=JWTAuth())


@router.post("/search", response={200: List[AvailableNumberSchema], 400: dict})
def search_numbers(request: HttpRequest, data: SearchNumberSchema):
    try:
        numbers = PhoneNumberService.search_available_numbers(data, request.auth)
        return 200, numbers
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/purchase", response={201: PhoneNumberOutSchema, 400: dict})
def purchase_number(request: HttpRequest, data: PurchaseNumberSchema):
    try:
        phone_number = PhoneNumberService.purchase_number(data, request.auth)
        phone_number = PhoneNumberService.get_number(str(phone_number.id), request.auth)
        return 201, PhoneNumberService.format_number(phone_number)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/import", response={201: PhoneNumberOutSchema, 400: dict})
def import_number(request: HttpRequest, data: PurchaseNumberSchema):
    try:
        phone_number = PhoneNumberService.import_existing_number(data, request.auth)
        phone_number = PhoneNumberService.get_number(str(phone_number.id), request.auth)
        return 201, PhoneNumberService.format_number(phone_number)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("", response={200: dict})
def list_numbers(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    numbers = PhoneNumberService.list_numbers(request.auth)
    data = [PhoneNumberService.format_number(n) for n in numbers]
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
    try:
        phone_number = PhoneNumberService.assign_number(number_id, data, request.auth)
        return 200, PhoneNumberService.format_number(phone_number)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/{number_id}/release", response={200: MessageResponseSchema, 400: dict, 404: dict})
def release_number(request: HttpRequest, number_id: str):
    try:
        PhoneNumberService.release_number(number_id, request.auth)
        return 200, {"message": "Number released successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}
