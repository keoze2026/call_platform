"""Active Bots endpoints (CH-477): every buyer's Stats API key on one page.

    GET  /api/active-bots/                       every buyer, its key state
    POST /api/active-bots/{buyer_id}/issue       new key, shown once in full
    POST /api/active-bots/{buyer_id}/switch-off  the buyer's key stops working
    POST /api/active-bots/{buyer_id}/copy        the live key again (CH-490)

Main account only, as every API key (CH-243). See accounts/buyer_bots.py.
"""
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from accounts.api import JWTAuth
from accounts.buyer_bots import bot_rows, copy_key, issue_key, switch_off
from accounts.main_user import require_main_user

router = Router(tags=["Active Bots"], auth=JWTAuth())


def _buyer(request, buyer_id):
    from buyers.models import Buyer
    try:
        return Buyer.objects.get(id=buyer_id, organization=request.auth.organization)
    except (Buyer.DoesNotExist, ValueError, ValidationError):
        raise HttpError(404, 'Buyer not found')


@router.get("/", response={200: dict})
def list_buyer_bots(request: HttpRequest):
    require_main_user(request.auth)
    rows = bot_rows(request.auth.organization)
    counts = {s: sum(1 for r in rows if r['status'] == s) for s in ('active', 'off', 'none')}
    return 200, {'items': rows, 'counts': counts}


@router.post("/{buyer_id}/issue", response={200: dict})
def issue_buyer_bot_key(request: HttpRequest, buyer_id: str):
    require_main_user(request.auth)
    return 200, issue_key(_buyer(request, buyer_id), request.auth)


@router.post("/{buyer_id}/switch-off", response={200: dict})
def switch_off_buyer_bot(request: HttpRequest, buyer_id: str):
    require_main_user(request.auth)
    return 200, switch_off(_buyer(request, buyer_id), request.auth)


@router.post("/{buyer_id}/copy", response={200: dict, 400: dict})
def copy_buyer_bot_key(request: HttpRequest, buyer_id: str):
    """CH-490: the buyer's live key in full, to copy again. POST, not GET, so
    it is never cached or kept in a browser history; logged every time."""
    require_main_user(request.auth)
    try:
        return 200, copy_key(_buyer(request, buyer_id), request.auth)
    except ValueError as e:
        return 400, {'detail': str(e)}
