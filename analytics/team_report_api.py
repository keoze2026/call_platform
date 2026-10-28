"""Team report on Telegram, from our own reports bot (CH-347).

Portal (workspace admins):
    GET    /api/team-report/                      the bot, and the connected chats
    POST   /api/team-report/connect-link          a link that connects a chat
    PATCH  /api/team-report/chats/{id}            {enabled?, send_time?}
    DELETE /api/team-report/chats/{id}            disconnect
    POST   /api/team-report/chats/{id}/send-test  today so far, now

Telegram (the reports bot's webhook; secret header required):
    POST   /api/team-report-bot/webhook
"""
import json
import logging
from datetime import datetime
from typing import Optional

from django.http import HttpRequest
from ninja import Router, Schema

from accounts.api import JWTAuth

logger = logging.getLogger(__name__)

router = Router(tags=["Team Report"], auth=JWTAuth())
bot_router = Router(tags=["Team Report Bot"])


class ChatIn(Schema):
    enabled: Optional[bool] = None
    send_time: Optional[str] = None


def _admin(request):
    from accounts.permissions import Capability, require
    require(request.auth, Capability.MEMBERS)


def _chat_out(c) -> dict:
    by = c.connected_by
    return {
        'id': str(c.id),
        'chat_id': c.chat_id,
        'title': c.title or c.chat_id,
        'kind': c.kind,
        'enabled': c.enabled,
        'send_time': c.send_time.strftime('%H:%M') if c.send_time else '09:00',
        'connected_at': c.connected_at.isoformat(),
        'connected_by': (by.get_full_name() or by.email) if by else '',
        'last_sent_at': c.last_sent_at.isoformat() if c.last_sent_at else None,
        'last_result': c.last_result,
    }


def _get(request, chat_id):
    from django.core.exceptions import ValidationError
    from analytics.models import TeamReportChat
    try:
        return TeamReportChat.objects.get(id=chat_id, organization=request.auth.organization)
    except (TeamReportChat.DoesNotExist, ValidationError, ValueError):
        return None


@router.get("/", response={200: dict})
def overview(request: HttpRequest):
    from analytics import team_report
    from analytics.models import TeamReportChat
    _admin(request)
    bot = team_report.bot_username() if team_report.configured() else ''
    chats = TeamReportChat.objects.filter(organization=request.auth.organization).select_related('connected_by')
    return 200, {'configured': team_report.configured(), 'bot': f'@{bot}' if bot else '',
                 'chats': [_chat_out(c) for c in chats]}


@router.post("/connect-link", response={200: dict, 400: dict})
def connect_link(request: HttpRequest):
    from analytics import team_report
    _admin(request)
    try:
        return 200, team_report.make_link(request.auth.organization, request.auth)
    except team_report.TeamReportError as e:
        return 400, {'detail': str(e)}


@router.patch("/chats/{chat_id}", response={200: dict, 400: dict, 404: dict})
def update_chat(request: HttpRequest, chat_id: str, payload: ChatIn):
    _admin(request)
    c = _get(request, chat_id)
    if c is None:
        return 404, {'detail': 'Chat not found'}
    if payload.enabled is not None:
        c.enabled = payload.enabled
    if payload.send_time is not None:
        try:
            c.send_time = datetime.strptime(payload.send_time.strip()[:5], '%H:%M').time()
        except ValueError:
            return 400, {'detail': 'Send time must look like 09:00'}
    c.save()
    return 200, _chat_out(c)


@router.delete("/chats/{chat_id}", response={200: dict, 404: dict})
def delete_chat(request: HttpRequest, chat_id: str):
    from analytics import team_report
    _admin(request)
    c = _get(request, chat_id)
    if c is None:
        return 404, {'detail': 'Chat not found'}
    if c.enabled:
        team_report.reply(c.chat_id, team_report.STOPPED_TEXT)
    c.delete()
    return 200, {'ok': True}


@router.post("/chats/{chat_id}/send-test", response={200: dict, 400: dict, 404: dict})
def send_test(request: HttpRequest, chat_id: str):
    from analytics import team_report
    _admin(request)
    c = _get(request, chat_id)
    if c is None:
        return 404, {'detail': 'Chat not found'}
    try:
        return 200, {'result': team_report.send(c, test=True)}
    except team_report.TeamReportError as e:
        return 400, {'detail': str(e)}


@bot_router.post("/webhook", response={200: dict, 403: dict})
def webhook(request: HttpRequest):
    """Updates for the reports bot. Telegram sends the secret set with
    setWebhook in every request; anything without it is refused."""
    from analytics import team_report
    secret = team_report.webhook_secret()
    if not secret or request.headers.get('X-Telegram-Bot-Api-Secret-Token', '') != secret:
        return 403, {'detail': 'forbidden'}
    try:
        team_report.handle_update(json.loads(request.body or b'{}'))
    except Exception:
        # Always 200: anything else makes Telegram send the same update again.
        logger.exception('reports bot webhook failed')
    return 200, {'ok': True}
