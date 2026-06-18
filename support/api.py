from ninja import Router, Schema
from django.http import HttpRequest
from typing import Optional

router = Router(tags=["Support"])


class StartSessionSchema(Schema):
    name: str = ''
    email: str = ''
    message: str


class SendMessageSchema(Schema):
    message: str


def send_telegram_support(text, session_id):
    import requests
    from django.conf import settings
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(settings, 'TELEGRAM_SUPPORT_CHAT_ID', '')
    if bot_token and chat_id:
        requests.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True},
            timeout=5
        )


@router.post("/chat", auth=None, response={201: dict, 422: dict})
def start_chat(request, payload: StartSessionSchema):
    from support.models import SupportSession, SupportMessage
    if not payload.message.strip():
        return 422, {"detail": "Message is required"}

    session = SupportSession.objects.create(
        name=payload.name,
        email=payload.email,
    )
    SupportMessage.objects.create(
        session=session,
        sender='visitor',
        message=payload.message,
    )

    # Notify support team on Telegram
    try:
        name = payload.name or 'Anonymous'
        email = f' ({payload.email})' if payload.email else ''
        tg_message = f'💬 New Support Chat\n👤 {name}{email}\n\nMessage: {payload.message}\n\nSession ID: {session.id}'
        send_telegram_support(tg_message, str(session.id))
    except Exception as e:
        print('Telegram support failed:', e)

    return 201, {
        'session_id': str(session.id),
        'message': 'Chat started. An agent will respond shortly.',
    }


@router.post("/chat/{session_id}", auth=None, response={200: dict, 404: dict})
def send_message(request, session_id: str, payload: SendMessageSchema):
    from support.models import SupportSession, SupportMessage
    try:
        session = SupportSession.objects.get(id=session_id)
        SupportMessage.objects.create(
            session=session,
            sender='visitor',
            message=payload.message,
        )
        try:
            tg_message = f'💬 New message from {session.name or "Anonymous"}\nSession: {session_id}\n\n{payload.message}\n\nReply: /reply {session_id} your message'
            send_telegram_support(tg_message, session_id)
        except Exception:
            pass
        return 200, {"message": "Message sent"}
    except SupportSession.DoesNotExist:
        return 404, {"detail": "Session not found"}


@router.get("/chat/{session_id}", auth=None, response={200: dict, 404: dict})
def get_messages(request, session_id: str):
    from support.models import SupportSession, SupportMessage
    try:
        session = SupportSession.objects.get(id=session_id)
        messages = SupportMessage.objects.filter(session=session)
        return 200, {
            'session_id': str(session.id),
            'status': session.status,
            'messages': [
                {
                    'id': str(m.id),
                    'sender': m.sender,
                    'message': m.message,
                    'created_at': m.created_at.isoformat(),
                }
                for m in messages
            ]
        }
    except SupportSession.DoesNotExist:
        return 404, {"detail": "Session not found"}


@router.post("/chat/{session_id}/webhook", auth=None, response={200: dict})
def telegram_webhook(request, session_id: str):
    """Telegram bot calls this when agent replies"""
    import json
    try:
        data = json.loads(request.body)
        reply_text = data.get('message', '')
        from support.models import SupportSession, SupportMessage
        session = SupportSession.objects.get(id=session_id)
        SupportMessage.objects.create(
            session=session,
            sender='agent',
            message=reply_text,
        )
        return 200, {"message": "Reply saved"}
    except Exception as e:
        return 200, {"message": str(e)}
