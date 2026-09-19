from ninja import Router
from django.http import HttpRequest
from typing import List
from accounts.api import JWTAuth
from .schemas import (
    CreateNotificationRuleSchema, UpdateNotificationRuleSchema,
    NotificationRuleOutSchema, NotificationLogOutSchema,
    MessageResponseSchema
)
from .services import NotificationService

router = Router(tags=["Notifications"], auth=JWTAuth())


@router.post("/rules", response={201: NotificationRuleOutSchema, 400: dict})
def create_rule(request: HttpRequest, data: CreateNotificationRuleSchema):
    try:
        rule = NotificationService.create_rule(data, request.auth)
        return 201, NotificationService.format_rule(rule)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/rules", response={200: dict})
def list_rules(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    rules = NotificationService.list_rules(request.auth)
    data = [NotificationService.format_rule(r) for r in rules]
    return 200, paginate_list(data, page, page_size)


@router.get("/rules/{rule_id}", response={200: NotificationRuleOutSchema, 404: dict})
def get_rule(request: HttpRequest, rule_id: str):
    try:
        rule = NotificationService.get_rule(rule_id, request.auth)
        return 200, NotificationService.format_rule(rule)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/rules/{rule_id}", response={200: NotificationRuleOutSchema, 400: dict, 404: dict})
def update_rule(request: HttpRequest, rule_id: str, data: UpdateNotificationRuleSchema):
    try:
        rule = NotificationService.update_rule(rule_id, data, request.auth)
        return 200, NotificationService.format_rule(rule)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/rules/{rule_id}", response={200: MessageResponseSchema, 404: dict})
def delete_rule(request: HttpRequest, rule_id: str):
    try:
        NotificationService.delete_rule(rule_id, request.auth)
        return 200, {"message": "Rule deleted successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.get("/logs", response={200: dict})
def list_logs(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    logs = NotificationService.list_logs(request.auth)
    data = [
        {
            'id': str(l.id),
            'event': l.event,
            'channel': l.channel,
            'recipient': l.recipient,
            'subject': l.subject,
            'status': l.status,
            'error': l.error,
            'created_at': l.created_at.isoformat(),
        }
        for l in logs
    ]
    return 200, paginate_list(data, page, page_size)

@router.get("/events", response={200: list})
def list_event_types(request: HttpRequest):
    """Catalogue of alert types, for rendering the pop-up preference list.

    Returned rather than hardcoded on the frontend so a new alert type appears
    in the settings automatically.
    """
    from notifications.models import NotificationRule, NotificationPreference

    defaults = set(NotificationPreference.DEFAULT_POPUP_EVENTS)
    return 200, [
        {
            'event': value,
            'label': label,
            'default_popup': value in defaults,
        }
        for value, label in NotificationRule.Event.choices
    ]


@router.get("/preferences", response={200: dict})
def get_preferences(request: HttpRequest):
    """This user's pop-up alert preferences."""
    from notifications.services import NotificationService
    return 200, NotificationService.get_preferences(request.auth)


@router.patch("/preferences", response={200: dict, 400: dict})
def update_preferences(request: HttpRequest):
    """Update which alerts pop up. Send any subset of the fields.

    Body: {"popups_enabled": bool, "popup_events": [str], "sound_enabled": bool}
    """
    import json as _json
    from notifications.services import NotificationService

    try:
        body = _json.loads(request.body or b'{}')
    except Exception:
        return 400, {"detail": "Invalid JSON"}

    try:
        return 200, NotificationService.update_preferences(request.auth, body)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/test", response={200: dict, 400: dict})
def test_notification(request: HttpRequest):
    try:
        NotificationService.dispatch('call.missed', request.auth.organization, {
            'caller_number': '+254700392123',
            'campaign_name': 'Test Campaign',
            'called_number': '+17275945570',
        })
        return 200, {"message": "Notification dispatched successfully", "success": True}
    except Exception as e:
        return 400, {"detail": str(e)}