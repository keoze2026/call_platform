"""Reports PIN endpoints. Mounted at /api/security/."""
from ninja import Router, Schema
from django.http import HttpRequest

from .services import PinError, lock, remove_pin, set_pin, status, verify

router = Router(tags=['security'])


class SetPinSchema(Schema):
    pin: str
    current_password: str = ''


class RemovePinSchema(Schema):
    current_password: str = ''


class VerifyPinSchema(Schema):
    pin: str


def _error(e: PinError):
    body = {'detail': e.detail, 'code': e.code}
    body.update(e.extra)
    return e.status, body


@router.get('/reports-pin/status', response={200: dict})
def pin_status(request: HttpRequest):
    """Never returns the PIN. Safe for any logged-in user."""
    return 200, status(request)


@router.put('/reports-pin', response={200: dict, 400: dict, 403: dict, 423: dict})
def pin_set(request: HttpRequest, data: SetPinSchema):
    try:
        return 200, set_pin(request, data.pin, data.current_password)
    except PinError as e:
        return _error(e)


@router.post('/reports-pin/remove', response={200: dict, 400: dict, 403: dict, 423: dict})
def pin_remove(request: HttpRequest, data: RemovePinSchema):
    # POST rather than DELETE-with-a-body: several proxies drop bodies on DELETE.
    try:
        return 200, remove_pin(request, data.current_password)
    except PinError as e:
        return _error(e)


@router.post('/reports-pin/verify', response={200: dict, 400: dict, 423: dict})
def pin_verify(request: HttpRequest, data: VerifyPinSchema):
    try:
        return 200, verify(request, data.pin)
    except PinError as e:
        return _error(e)


@router.post('/reports-pin/lock', response={200: dict})
def pin_lock(request: HttpRequest):
    return 200, lock(request)
