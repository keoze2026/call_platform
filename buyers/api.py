from accounts.permissions import require, Capability
from django.conf import settings
from ninja import Router, Schema
from django.http import HttpRequest
from typing import List, Optional
from .schemas import (
    CreateBuyerSchema, UpdateBuyerSchema,
    BuyerOutSchema, BuyerListOutSchema,
    BuyerCapSchema, BuyerStatsSchema,
    AssignCampaignSchema, MessageResponseSchema
)
from .services import BuyerService
from .models import Buyer
from accounts.api import JWTAuth

router = Router(tags=["Buyers"], auth=JWTAuth())


# ── The buyer's own Telegram report switch (CH-259) ─────────────────────────
# Sir, 25 Oct: a report goes out automatically only for a buyer whose own
# account ticked the box approving it; the buyer can turn it off any time.
# Defined before the /{buyer_id} routes so "my" is never taken for an id.

class MyTelegramReportSchema(Schema):
    approved: bool


def _my_buyer(request):
    user = request.auth
    if getattr(user, 'role', '') != 'buyer' or not getattr(user, 'buyer_id', None):
        return None
    return user.buyer


def _my_report_out(r) -> dict:
    from buyers.telegram_report import bot_username
    return {
        "approved": r.buyer_approved,
        "approved_at": r.buyer_approved_at.isoformat() if r.buyer_approved_at else None,
        # Set up by the workspace: whether a chat and time are there yet. The
        # chat ID itself is not shown back.
        "set_up": bool(r.enabled and r.chat_id),
        # CH-310: a Telegram chat is connected (the buyer can connect it itself).
        "chat_connected": bool(r.chat_id),
        "send_time": r.send_time.strftime('%H:%M'),
        "no_recording_link": r.no_recording_link,
        "bot": bot_username(),
        "last_sent_at": r.last_sent_at.isoformat() if r.last_sent_at else None,
    }


@router.get("/telegram-reports", response={200: dict})
def list_telegram_reports(request: HttpRequest):
    """Every buyer of the workspace with its Telegram report status, for the
    Automated Reports page (CH-261). Workspace admins only. Defined before the
    /{buyer_id} routes so "telegram-reports" is never taken for an id."""
    from buyers.models import BuyerTelegramReport
    require(request.auth, Capability.MEMBERS)
    reports = {r.buyer_id: r for r in BuyerTelegramReport.objects.filter(buyer__organization=request.auth.organization)}
    items = []
    for b in Buyer.objects.filter(organization=request.auth.organization).exclude(status='archived').order_by('name'):
        r = reports.get(b.id)
        if r is None or not r.chat_id:
            state = 'not_set_up'
        elif not r.enabled:
            state = 'off'
        elif not r.buyer_approved:
            state = 'waiting_approval'
        else:
            state = 'active'
        items.append({
            "buyer_id": str(b.id),
            "buyer_name": b.name,
            "state": state,
            "send_time": r.send_time.strftime('%H:%M') if r else None,
            "last_sent_at": r.last_sent_at.isoformat() if r and r.last_sent_at else None,
            "last_result": r.last_result if r else '',
        })
    order = {'active': 0, 'waiting_approval': 1, 'off': 2, 'not_set_up': 3}
    items.sort(key=lambda i: (order[i['state']], i['buyer_name'].lower()))
    counts = {k: sum(1 for i in items if i['state'] == k) for k in order}
    return 200, {"items": items, "counts": counts}


@router.get("/my/telegram-report", response={200: dict, 403: dict})
def get_my_telegram_report(request: HttpRequest):
    """A buyer login's own report switch."""
    from buyers.models import BuyerTelegramReport
    buyer = _my_buyer(request)
    if buyer is None:
        return 403, {"detail": "Only a buyer's own login has this switch."}
    r, _ = BuyerTelegramReport.objects.get_or_create(buyer=buyer)
    return 200, _my_report_out(r)


@router.patch("/my/telegram-report", response={200: dict, 403: dict})
def set_my_telegram_report(request: HttpRequest, data: MyTelegramReportSchema):
    """Approve, or stop, the automatic report. Only the buyer itself may."""
    from django.utils import timezone as _tz
    from buyers.models import BuyerTelegramReport
    buyer = _my_buyer(request)
    if buyer is None:
        return 403, {"detail": "Only a buyer's own login has this switch."}
    r, _ = BuyerTelegramReport.objects.get_or_create(buyer=buyer)
    r.buyer_approved = data.approved
    r.buyer_approved_at = _tz.now() if data.approved else None
    r.buyer_approved_by = (request.auth.email or '')[:254] if data.approved else ''
    r.save(update_fields=['buyer_approved', 'buyer_approved_at', 'buyer_approved_by', 'updated_at'])
    return 200, _my_report_out(r)


@router.post("/my/telegram-report/connect-link", response={200: dict, 403: dict})
def my_telegram_connect_link(request: HttpRequest):
    """CH-310: the buyer connects its own Telegram chat - a link to our bot,
    for a private chat or a group. No chat ID to look up or send to anyone."""
    from buyers.telegram_connect import make_link
    buyer = _my_buyer(request)
    if buyer is None:
        return 403, {"detail": "Only a buyer's own login has this."}
    return 200, make_link(buyer)


# Plain dict rather than BuyerOutSchema so the `invite` block reaches the
# caller - a schema would filter it out, and whether the invitation went is what
# the person who just filled in the form wants to know.
@router.post("", response={201: dict, 400: dict})
def create_buyer(request: HttpRequest, data: CreateBuyerSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'edit')
    require(request.auth, Capability.CREATE)
    try:
        created = BuyerService.create(data, request.auth)
        # Carried off the in-memory object before the re-read drops it.
        invite = getattr(created, 'invite_result', None)
        buyer = BuyerService.get_buyer(str(created.id), request.auth)
        body = BuyerService.format_buyer(buyer)
        if invite is not None:
            body['invite'] = invite
        return 201, body
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("", response={200: dict})
def list_buyers(request: HttpRequest, page: int = 1, page_size: int = 50):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'view')
    from config.pagination import paginate_list
    from datetime import timedelta
    from decimal import Decimal
    from django.db.models import Count, Q, Sum
    from django.db.models.functions import Coalesce
    from django.utils import timezone as djtz
    from analytics.models import CallRecord

    buyers = BuyerService.list_buyers(request.auth)

    # The Manage Buyers screen (the owner's ask) needs the four spend windows
    # on every row. The page used to fan out one /stats call per buyer - 54
    # buyers, 54 requests. One grouped query, the SAME definitions as the
    # per-buyer stats endpoint above, so the row and the detail page can
    # never disagree.
    now = djtz.now()
    hour_ago = now - timedelta(hours=1)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    by_buyer = {
        r['buyer_id']: r for r in
        CallRecord.objects.filter(organization=request.auth.organization,
                                  buyer_id__isnull=False)
        .values('buyer_id')
        .annotate(
            calls_hourly=Count('id', filter=Q(created_at__gte=hour_ago)),
            calls_today=Count('id', filter=Q(created_at__gte=today)),
            calls_month=Count('id', filter=Q(created_at__gte=month_start)),
            calls_global=Count('id'),
            spend_hourly=Coalesce(Sum('payout', filter=Q(created_at__gte=hour_ago)), Decimal('0.00')),
            spend_today=Coalesce(Sum('payout', filter=Q(created_at__gte=today)), Decimal('0.00')),
            spend_month=Coalesce(Sum('payout', filter=Q(created_at__gte=month_start)), Decimal('0.00')),
            spend_global=Coalesce(Sum('payout'), Decimal('0.00')),
        )
    }
    zero = Decimal('0.00')
    data = []
    for b in buyers:
        s = by_buyer.get(b.id, {})
        data.append({
            'id': str(b.id),
            'name': b.name,
            'status': b.status,
            'routing_type': b.routing_type,
            'phone_number': b.phone_number,
            'payout_amount': str(b.payout_amount),
            'max_concurrency': b.max_concurrency,
            'created_at': b.created_at.isoformat(),
            'calls_hourly': s.get('calls_hourly', 0),
            'calls_today': s.get('calls_today', 0),
            'calls_month': s.get('calls_month', 0),
            'calls_global': s.get('calls_global', 0),
            'spend_hourly': str(s.get('spend_hourly', zero)),
            'spend_today': str(s.get('spend_today', zero)),
            'spend_month': str(s.get('spend_month', zero)),
            'spend_global': str(s.get('spend_global', zero)),
        })
    return 200, paginate_list(data, page, page_size)


@router.get("/{buyer_id}", response={200: BuyerOutSchema, 404: dict})
def get_buyer(request: HttpRequest, buyer_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'view')
    try:
        buyer = BuyerService.get_buyer(buyer_id, request.auth)
        return 200, BuyerService.format_buyer(buyer)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/{buyer_id}", response={200: BuyerOutSchema, 400: dict, 404: dict})
def update_buyer(request: HttpRequest, buyer_id: str, data: UpdateBuyerSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'edit')
    require(request.auth, Capability.EDIT)
    try:
        BuyerService.update(buyer_id, data, request.auth)
        buyer = BuyerService.get_buyer(buyer_id, request.auth)
        return 200, BuyerService.format_buyer(buyer)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.delete("/{buyer_id}", response={200: MessageResponseSchema, 404: dict})
def delete_buyer(request: HttpRequest, buyer_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'delete')
    require(request.auth, Capability.DELETE)
    try:
        BuyerService.delete(buyer_id, request.auth)
        return 200, {"message": "Buyer archived successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{buyer_id}/pause", response={200: MessageResponseSchema, 400: dict, 404: dict})
def pause_buyer(request: HttpRequest, buyer_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'edit')
    require(request.auth, Capability.CREATE)
    try:
        BuyerService.pause(buyer_id, request.auth)
        return 200, {"message": "Buyer paused successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/{buyer_id}/activate", response={200: MessageResponseSchema, 400: dict, 404: dict})
def activate_buyer(request: HttpRequest, buyer_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'edit')
    require(request.auth, Capability.CREATE)
    try:
        BuyerService.activate(buyer_id, request.auth)
        return 200, {"message": "Buyer activated successfully", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.patch("/{buyer_id}/cap", response={200: dict, 400: dict, 404: dict})
def update_cap(request: HttpRequest, buyer_id: str, data: BuyerCapSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'edit')
    require(request.auth, Capability.EDIT)
    try:
        cap = BuyerService.update_cap(buyer_id, data, request.auth)
        return 200, {
            'id': str(cap.id),
            'max_calls_daily': cap.max_calls_daily,
            'max_calls_monthly': cap.max_calls_monthly,
            'max_calls_global': cap.max_calls_global,
            'max_concurrency': cap.max_concurrency,
        }
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{buyer_id}/campaigns", response={200: dict, 400: dict, 404: dict})
def assign_campaign(request: HttpRequest, buyer_id: str, data: AssignCampaignSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'edit')
    require(request.auth, Capability.CREATE)
    try:
        assignment = BuyerService.assign_campaign(buyer_id, data, request.auth)
        return 200, {
            'id': str(assignment.id),
            'campaign_id': str(assignment.campaign_id),
            'campaign_name': assignment.campaign.name,
            'priority': assignment.priority,
            'weight': assignment.weight,
            'is_active': assignment.is_active,
        }
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/{buyer_id}/campaigns/{campaign_id}", response={200: MessageResponseSchema, 404: dict})
def remove_campaign(request: HttpRequest, buyer_id: str, campaign_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'delete')
    require(request.auth, Capability.DELETE)
    try:
        BuyerService.remove_campaign(buyer_id, campaign_id, request.auth)
        return 200, {"message": "Buyer removed from campaign successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.get("/{buyer_id}/stats", response={200: BuyerStatsSchema, 404: dict})
def get_stats(request: HttpRequest, buyer_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'view')
    try:
        stats = BuyerService.get_stats(buyer_id, request.auth)
        return 200, stats
    except ValueError as e:
        return 404, {"detail": str(e)}

@router.delete("/{buyer_id}/campaigns/{campaign_id}", response={200: dict, 404: dict})
def detach_campaign(request: HttpRequest, buyer_id: str, campaign_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'delete')
    require(request.auth, Capability.DELETE)
    from buyers.models import BuyerCampaign
    try:
        # Scoped to the caller's organization: without it any authenticated user
        # could delete another organization's assignment by its ids alone.
        bc = BuyerCampaign.objects.get(
            buyer_id=buyer_id,
            campaign_id=campaign_id,
            buyer__organization=request.auth.organization,
        )
        bc.delete()
        return 200, {"message": "Campaign detached", "success": True}
    except BuyerCampaign.DoesNotExist:
        return 404, {"detail": "Assignment not found"}


@router.post("/{buyer_id}/invite", response={200: dict, 400: dict, 404: dict})
def invite_buyer(request: HttpRequest, buyer_id: str):
    """Send the buyer a link to set up their own login.

    The previous version emailed `buyer.created_by.email` - the admin who created
    the record, not the buyer - and generated a token it never stored, so the
    link it sent could never be validated. It also ignored the email address the
    form collects.
    """
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'edit')
    import json

    from accounts.partner_invites import InviteError, invite_partner
    from buyers.models import Buyer as BuyerModel

    require(request.auth, Capability.CREATE)

    try:
        body = json.loads(request.body or b'{}')
    except Exception:
        body = {}

    try:
        buyer = BuyerModel.objects.get(id=buyer_id, organization=request.auth.organization)
    except BuyerModel.DoesNotExist:
        return 404, {"detail": "Buyer not found"}

    # The form's address first; the record's own as a fallback. Buyer stores it
    # as contact_email, not email.
    from accounts.auto_invite import partner_email
    email = body.get('email') or partner_email(buyer)

    try:
        return 200, invite_partner(
            organization=request.auth.organization,
            partner=buyer,
            kind='buyer',
            email=email,
            contact_name=body.get('contact_name') or body.get('name') or '',
            invited_by=request.auth,
        )
    except InviteError as e:
        return 400, {"detail": str(e)}


@router.get("/{buyer_id}/access", response={200: dict, 404: dict})
def get_buyer_access(request, buyer_id: str):
    """The buyer's time zone and what its own logins may do. Admin-side."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'view')
    # Workspace admins only (CH-259): partner logins skip the route table, so
    # a buyer login with Manage Traffic could read every buyer's access here
    # and grant itself Download Reports.
    require(request.auth, Capability.MEMBERS)
    from buyers.models import Buyer
    try:
        b = Buyer.objects.get(id=buyer_id, organization=request.auth.organization)
    except Buyer.DoesNotExist:
        return 404, {"detail": "Buyer not found"}
    from accounts.partner_permissions import clean_permissions
    perms = clean_permissions(b.permissions)
    return 200, {
        "timezone": _tz_effective(b),
        "timezone_is_own": _tz_is_own(b),
        "permissions": {
            "block_numbers": perms.get('block_numbers', False),
            "download_reports": perms.get('download_reports', False),
        },
    }


def _tz_effective(b):
    from accounts.partner_timezone import effective
    return effective(b)


def _tz_is_own(b):
    from accounts.partner_timezone import is_own
    return is_own(b)


class _AccessPermissionsSchema(Schema):
    block_numbers: Optional[bool] = None
    download_reports: Optional[bool] = None


class _AccessPatchSchema(Schema):
    timezone: Optional[str] = None
    permissions: Optional[_AccessPermissionsSchema] = None


@router.patch("/{buyer_id}/access", response={200: dict, 400: dict, 404: dict})
def update_buyer_access(request, buyer_id: str, payload: _AccessPatchSchema):
    """Save time zone / permissions. Only the fields sent are touched.

    A bare `payload: dict` here made Ninja expect the body wrapped as
    {"payload": {...}} and 422 every normal request - the toggles looked
    saved and were not (Evans's catch). A Schema accepts the plain body.
    """
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'edit')
    require(request.auth, Capability.MEMBERS)   # admins only - see get_buyer_access
    payload = payload.dict(exclude_none=True)
    require(request.auth, Capability.EDIT)
    from buyers.models import Buyer
    try:
        b = Buyer.objects.get(id=buyer_id, organization=request.auth.organization)
    except Buyer.DoesNotExist:
        return 404, {"detail": "Buyer not found"}

    fields = []
    if 'timezone' in payload and payload['timezone'] is not None:
        from accounts.partner_timezone import to_store
        try:
            b.timezone = to_store(str(payload['timezone']))
        except Exception:
            return 400, {"detail": "Unknown time zone"}
        fields.append('timezone')
    # One store: the permissions JSON the capability system reads. The
    # booleans stay as mirrors so nothing old breaks - never the source
    # (Evans's one-source-of-truth point).
    from accounts.partner_permissions import clean_permissions
    perms_in = payload.get('permissions') or {}
    stored = clean_permissions(b.permissions)
    for key in ('block_numbers', 'download_reports'):
        if key in perms_in and perms_in[key] is not None:
            stored[key] = bool(perms_in[key])
    b.permissions = stored
    b.can_block_numbers = stored.get('block_numbers', False)
    b.can_download_reports = stored.get('download_reports', False)
    fields += ['permissions', 'can_block_numbers', 'can_download_reports']
    b.save(update_fields=list(set(fields)))
    return 200, {
        "timezone": _tz_effective(b),
        "timezone_is_own": _tz_is_own(b),
        "permissions": {
            "block_numbers": stored.get('block_numbers', False),
            "download_reports": stored.get('download_reports', False),
        },
    }


# (reporting-config, the Redis-cached reporting visibility, lived here and is
# removed: the frontend reads visibility from /me and saves it through the
# partner settings endpoint - one store, accounts/partner_permissions.)

class RemoveMemberSchema(Schema):
    email: str




@router.get("/{buyer_id}/members", response={200: list, 404: dict})
def list_buyer_members(request: HttpRequest, buyer_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'view')
    """Who can actually sign in to this buyer - read from the server.

    The Members box on the edit page was browser storage: it only remembered
    people invited from that box on that machine, while logins created any
    other way (auto-invite on create, another browser, cleared storage) kept
    working invisibly. This is the real list, from the users table, so what
    the admin sees is what the database enforces.
    """
    from accounts.models import User
    from buyers.models import Buyer
    from django.core.exceptions import ValidationError

    try:
        partner = Buyer.objects.get(
            id=buyer_id, organization=request.auth.organization)
    except (Buyer.DoesNotExist, ValidationError, ValueError):
        return 404, {"detail": "Buyer not found"}

    rows = []
    for u in User.objects.filter(buyer_id=partner.id).order_by('email'):
        rows.append({
            'id': str(u.id),
            'email': u.email,
            'is_active': u.is_active,
            'invite_status': u.invite_status,
            'invited_at': u.invited_at.isoformat() if u.invited_at else None,
            'accepted_at': u.accepted_at.isoformat() if u.accepted_at else None,
            'last_login': u.last_login.isoformat() if u.last_login else None,
        })
    return 200, rows

@router.post("/{buyer_id}/members/remove", response={200: dict, 403: dict, 404: dict})
def remove_buyer_member(request: HttpRequest, buyer_id: str, data: RemoveMemberSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'buyers', 'edit')
    """Take one person's access to this buyer away.

    The trash icon in the Members table had no endpoint behind it, so removing
    somebody changed the browser and nothing else - they kept their password and
    their open sessions.

    `member_not_found` is a distinct code because the interface treats it as
    "already removed" and clears the row, while a missing endpoint (a plain 404)
    means the login is still live and it has to say so.
    """
    from accounts.models import User
    from accounts.partner_access import erase_partner_member

    require(request.auth, Capability.EDIT)

    email = (data.email or '').strip().lower()
    user = User.objects.filter(
        email__iexact=email,
        buyer_id=buyer_id,
        organization=request.auth.organization,
    ).first()
    if user is None:
        return 404, {"detail": f"{email} is not a member of this buyer.",
                     "code": "member_not_found"}

    if not erase_partner_member(user, reason='removed from buyer'):
        return 403, {"detail": "That account is a staff login and was not changed.",
                     "code": "not_a_partner"}

    return 200, {"detail": "removed"}


# ── Telegram report (CH-258) ────────────────────────────────────────────────

class TelegramReportSchema(Schema):
    enabled: Optional[bool] = None
    chat_id: Optional[str] = None
    send_time: Optional[str] = None          # "21:00", Eastern
    no_recording_link: Optional[bool] = None
    connected_only: Optional[bool] = None
    hide_fail_reason: Optional[bool] = None
    included_columns: Optional[List[str]] = None    # column keys, CH-267
    # Lists of pairs, not maps: the portal's client renames object keys
    # (snake_case <-> camelCase), which would mangle "No Destination".
    column_names: Optional[List[dict]] = None       # [{"key": "status", "label": "Call Status"}]
    comment_rewrites: Optional[List[dict]] = None   # [{"from": "No Destination", "to": "Line Busy"}]


# Workspace admins only - checked here, not only in the route table: buyer and
# publisher logins skip the route table by design (Partner Access governs
# them), so a buyer login with Manage Traffic on could otherwise open, and
# redirect, another buyer's report (CH-259).
def _telegram_report(request, buyer_id):
    from buyers.models import BuyerTelegramReport
    try:
        buyer = Buyer.objects.get(id=buyer_id, organization=request.auth.organization)
    except (Buyer.DoesNotExist, ValueError):
        return None
    report, _ = BuyerTelegramReport.objects.get_or_create(buyer=buyer)
    return report


def _telegram_report_out(r) -> dict:
    from buyers.telegram_report import COLUMNS, DEFAULT_COLUMNS
    return {
        "enabled": r.enabled,
        "chat_id": r.chat_id,
        "send_time": r.send_time.strftime('%H:%M'),
        "no_recording_link": r.no_recording_link,
        "connected_only": r.connected_only,
        "hide_fail_reason": r.hide_fail_reason,
        "column_names": [{"key": k, "label": v} for k, v in (r.column_names or {}).items()],
        "comment_rewrites": [{"from": k, "to": v} for k, v in (r.comment_rewrites or {}).items()],
        "columns": [{"key": k, "label": label} for k, label in COLUMNS],
        "included_columns": list(r.included_columns or DEFAULT_COLUMNS),
        "last_sent_at": r.last_sent_at.isoformat() if r.last_sent_at else None,
        "last_result": r.last_result,
        "buyer_approved": r.buyer_approved,
        "buyer_approved_at": r.buyer_approved_at.isoformat() if r.buyer_approved_at else None,
        "buyer_approved_by": r.buyer_approved_by,
    }


@router.get("/{buyer_id}/telegram-report", response={200: dict, 404: dict})
def get_telegram_report(request: HttpRequest, buyer_id: str):
    """The buyer's daily Telegram report settings. Admin-side."""
    require(request.auth, Capability.MEMBERS)
    r = _telegram_report(request, buyer_id)
    if r is None:
        return 404, {"detail": "Buyer not found"}
    return 200, _telegram_report_out(r)


@router.patch("/{buyer_id}/telegram-report", response={200: dict, 400: dict, 404: dict})
def update_telegram_report(request: HttpRequest, buyer_id: str, data: TelegramReportSchema):
    """Save the report settings. Only the fields sent are touched."""
    import re
    from datetime import datetime as _dt
    from buyers.telegram_report import ReportError, check_column_names, check_rewrites
    require(request.auth, Capability.MEMBERS)
    r = _telegram_report(request, buyer_id)
    if r is None:
        return 404, {"detail": "Buyer not found"}
    try:
        if data.chat_id is not None:
            chat = data.chat_id.strip()
            if chat and not re.fullmatch(r'-?\d{3,20}|@[A-Za-z0-9_]{5,32}', chat):
                raise ReportError('A Telegram chat ID is a number (e.g. 123456789, or -100... for a group) or @channelname.')
            r.chat_id = chat
        if data.send_time is not None:
            try:
                r.send_time = _dt.strptime(data.send_time.strip(), '%H:%M').time()
            except ValueError:
                raise ReportError('Send time must look like 21:00.')
        for f in ('enabled', 'no_recording_link', 'connected_only', 'hide_fail_reason'):
            v = getattr(data, f)
            if v is not None:
                setattr(r, f, v)
        if data.included_columns is not None:
            from buyers.telegram_report import COLUMN_KEYS
            keys = [k for k in data.included_columns if k in COLUMN_KEYS]
            if not keys:
                raise ReportError('Tick at least one column.')
            r.included_columns = keys
        if data.column_names is not None:
            r.column_names = check_column_names(
                {str(i.get('key', '')): i.get('label', '') for i in data.column_names if isinstance(i, dict)})
        if data.comment_rewrites is not None:
            r.comment_rewrites = check_rewrites(
                {str(i.get('from', '')): i.get('to', '') for i in data.comment_rewrites if isinstance(i, dict)})
        if r.enabled and not r.chat_id:
            raise ReportError('Add the buyer\'s Telegram chat ID before switching the report on.')
    except ReportError as e:
        return 400, {"detail": str(e)}
    r.save()
    return 200, _telegram_report_out(r)


@router.post("/{buyer_id}/telegram-report/send", response={200: dict, 400: dict, 404: dict})
def send_telegram_report_now(request: HttpRequest, buyer_id: str):
    """Send the report now - the period its scheduled report covers - to
    check the buyer's setup. Does not count as today's scheduled report."""
    from django.utils import timezone as _tz
    from buyers.telegram_report import ReportError, send_report
    require(request.auth, Capability.MEMBERS)
    r = _telegram_report(request, buyer_id)
    if r is None:
        return 404, {"detail": "Buyer not found"}
    try:
        result = send_report(r)
    except ReportError as e:
        r.last_result = f'Not sent: {e}'[:255]
        r.save(update_fields=['last_result', 'updated_at'])
        return 400, {"detail": str(e)}
    r.last_sent_at, r.last_result = _tz.now(), f'{result} (sent by hand)'
    r.save(update_fields=['last_sent_at', 'last_result', 'updated_at'])
    return 200, _telegram_report_out(r)



@router.post("/{buyer_id}/telegram-report/connect-link", response={200: dict, 404: dict})
def telegram_connect_link(request: HttpRequest, buyer_id: str):
    """CH-310: a connect link to send the buyer. When the buyer opens it (or
    adds our bot to a group with it), its chat is saved here by our bot."""
    from buyers.telegram_connect import make_link
    require(request.auth, Capability.MEMBERS)
    r = _telegram_report(request, buyer_id)
    if r is None:
        return 404, {"detail": "Buyer not found"}
    return 200, make_link(r.buyer)


@router.post("/{buyer_id}/telegram-report/send-test", response={200: dict, 400: dict, 404: dict})
def send_telegram_report_test(request: HttpRequest, buyer_id: str):
    """CH-310: send this buyer's report to the admin's OWN Telegram (linked in
    Settings), to see exactly what the buyer gets. Nothing goes to the buyer."""
    from buyers.telegram_report import ReportError, send_report
    require(request.auth, Capability.MEMBERS)
    r = _telegram_report(request, buyer_id)
    if r is None:
        return 404, {"detail": "Buyer not found"}
    mine = (getattr(request.auth, 'telegram_chat_id', '') or '').strip()
    if not mine:
        return 400, {"detail": "Link your own Telegram first: Settings > Profile > Telegram."}
    try:
        result = send_report(r, test_chat=mine)
    except ReportError as e:
        return 400, {"detail": str(e)}
    return 200, {"detail": f'{result} to your Telegram (test).'}
