from ninja import Router
from django.http import HttpRequest
from .schemas import (
    ReferralProgramOutSchema, ReferralOutSchema,
    ReferralStatsSchema, SpendingTrackerSchema,
    InviteSchema, MessageResponseSchema
)
from .services import ReferralService
from accounts.api import JWTAuth

router = Router(tags=["Referrals"], auth=JWTAuth())


@router.get("/", response={200: ReferralProgramOutSchema})
def get_program(request: HttpRequest):
    program = ReferralService.get_program(request.auth)
    return 200, ReferralService.format_program(program)


@router.get("/stats", response={200: ReferralStatsSchema})
def get_stats(request: HttpRequest):
    return 200, ReferralService.get_stats(request.auth)


@router.get("/referred-clients", response={200: list})
def list_referrals(request: HttpRequest):
    referrals = ReferralService.list_referrals(request.auth)
    return 200, [ReferralService.format_referral(r) for r in referrals]


@router.get("/spending-tracker", response={200: list})
def spending_tracker(request: HttpRequest, days: int = 30):
    return 200, ReferralService.get_spending_tracker(request.auth, days)


@router.post("/invite", response={200: MessageResponseSchema, 400: dict})
def send_invite(request: HttpRequest, data: InviteSchema):
    try:
        ReferralService.send_invite(request.auth, data.email, data.name)
        return 200, {"message": "Invite sent successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}
