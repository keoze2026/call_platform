from ninja import File, Form, Router, Schema
from ninja.files import UploadedFile
from django.conf import settings
import logging
logger = logging.getLogger(__name__)
from typing import Optional

router = Router(tags=["Support"])


class StartSessionSchema(Schema):
    name: str = ''
    email: str = ''
    message: str
    # CH-669: the browser's own random id (kept in that browser), so the same
    # person is known in a new chat.
    browser_id: str = ''


class SendMessageSchema(Schema):
    message: str
    browser_id: str = ''


def _who(request, browser_id='') -> dict:
    """CH-669: internet address, browser id and browser of the visitor."""
    from config.ratelimit import client_ip
    return {'client_ip': (client_ip(None, request) or '')[:45],
            'browser_id': (browser_id or '').strip()[:64],
            'user_agent': (request.META.get('HTTP_USER_AGENT') or '')[:300]}


def send_telegram_support(text, session_id):
    import requests
    from django.conf import settings
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(settings, 'TELEGRAM_SUPPORT_CHAT_ID', '')
    if bot_token and chat_id:
        requests.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
                **({'message_thread_id': int(settings.TELEGRAM_SUPPORT_THREAD_ID)}
                   if getattr(settings, 'TELEGRAM_SUPPORT_THREAD_ID', '') else {}),
            },
            timeout=5
        )


@router.post("/chat", auth=None, response={201: dict, 422: dict})
def start_chat(request, payload: StartSessionSchema):
    from django_ratelimit.decorators import is_ratelimited
    if is_ratelimited(request, group='support_chat_start', key='config.ratelimit.client_ip',
                      rate=getattr(settings, 'SUPPORT_CHAT_RATE', '5/m'),
                      method='POST', increment=True):
        return 422, {"detail": "Too many chats started. Please wait a moment."}
    from support.models import SupportSession, SupportMessage
    if not payload.message.strip():
        return 422, {"detail": "Message is required"}

    session = SupportSession.objects.create(
        name=payload.name,
        email=payload.email,
        **_who(request, payload.browser_id),
    )
    # CH-669: greetings only, more than SUPPORT_GREETING_LIMIT times: kept,
    # not sent to the team.
    from support import greetings
    greeting, held = greetings.check(session, payload.message)
    SupportMessage.objects.create(
        session=session,
        sender='visitor',
        message=payload.message,
        greeting=greeting,
        held=held,
    )
    if held:
        return 201, {'session_id': str(session.id), 'message': greetings.NOTICE, 'blocked': True}

    try:
        name = payload.name or 'Anonymous'
        email = f' ({payload.email})' if payload.email else ''
        prefix = str(session.id)[:8]
        tg_message = (
            f'💬 <b>New Support Chat</b>\n'
            f'👤 <b>{name}</b>{email}\n\n'
            f'<b>Message:</b> {payload.message}\n\n'
            f'─────────────────\n'
            f'📨 <b>To reply, send:</b>\n'
            f'<code>{prefix} your reply here</code>'
        )
        send_telegram_support(tg_message, str(session.id))
    except Exception as e:
        print('Telegram support failed:', e)

    return 201, {
        'session_id': str(session.id),
        'message': 'Chat started. An agent will respond shortly.',
    }


@router.post("/chat/{session_id}", auth=None, response={200: dict, 404: dict})
def send_message(request, session_id: str, payload: SendMessageSchema):
    from django_ratelimit.decorators import is_ratelimited
    if is_ratelimited(request, group='support_chat_msg', key='config.ratelimit.client_ip',
                      rate=getattr(settings, 'SUPPORT_MESSAGE_RATE', '30/m'),
                      method='POST', increment=True):
        return 404, {"detail": "Too many messages. Please slow down."}
    from support.models import SupportSession, SupportMessage
    try:
        session = SupportSession.objects.get(id=session_id)
        # CH-669: who is writing now (kept up to date), and greetings only.
        who = _who(request, payload.browser_id)
        who = {k: v for k, v in who.items() if v}
        if who:
            SupportSession.objects.filter(pk=session.pk).update(**who)
            for k, v in who.items():
                setattr(session, k, v)
        from support import greetings
        greeting, held = greetings.check(session, payload.message)
        SupportMessage.objects.create(
            session=session,
            sender='visitor',
            message=payload.message,
            greeting=greeting,
            held=held,
        )
        if held:
            return 200, {'message': greetings.NOTICE, 'blocked': True}
        try:
            name = session.name or 'Anonymous'
            prefix = str(session.id)[:8]
            tg_message = (
                f'💬 <b>Follow-up from {name}</b>\n\n'
                f'<b>Message:</b> {payload.message}\n\n'
                f'─────────────────\n'
                f'📨 <b>To reply, send:</b>\n'
                f'<code>{prefix} your reply here</code>'
            )
            send_telegram_support(tg_message, session_id)
        except Exception:
            # The ticket is saved, but nobody was pinged about it.
            logger.exception('support telegram notification failed: session=%s', session_id)
        return 200, {"message": "Message sent"}
    except SupportSession.DoesNotExist:
        return 404, {"detail": "Session not found"}


@router.get("/chat/{session_id}", auth=None, response={200: dict, 404: dict})
def get_messages(request, session_id: str):
    from support.models import SupportSession, SupportMessage
    try:
        session = SupportSession.objects.get(id=session_id)
        # CH-654: the pictures themselves are never loaded here - only where to get them.
        messages = SupportMessage.objects.filter(session=session).defer('image')
        from support.images import url_of
        return 200, {
            'session_id': str(session.id),
            'status': session.status,
            'messages': [
                {
                    'id': str(m.id),
                    'sender': m.sender,
                    'message': m.message,
                    'image_url': url_of(m),
                    'created_at': m.created_at.isoformat(),
                }
                for m in messages
            ]
        }
    except SupportSession.DoesNotExist:
        return 404, {"detail": "Session not found"}


def _uuid(value: str) -> bool:
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


@router.post("/chat/{session_id}/image", auth=None, response={200: dict, 404: dict, 413: dict, 415: dict})
def send_image(request, session_id: str, image: UploadedFile = File(...), message: str = Form('')):
    """CH-654: the visitor's picture (pasted or picked), up to 2 MB, with an
    optional line of text. Kept with the chat and sent to the support group
    as a photo. Same limit as a text message (the same counter)."""
    from django_ratelimit.decorators import is_ratelimited
    from support import images
    from support.models import SupportMessage, SupportSession
    if is_ratelimited(request, group='support_chat_msg', key='config.ratelimit.client_ip',
                      rate=getattr(settings, 'SUPPORT_MESSAGE_RATE', '30/m'),
                      method='POST', increment=True):
        return 404, {"detail": "Too many messages. Please slow down."}
    session = SupportSession.objects.filter(id=session_id).first() if _uuid(session_id) else None
    if session is None:
        return 404, {"detail": "Session not found"}
    if (image.size or 0) > images.MAX_BYTES:
        return 413, {"detail": images.TOO_BIG}
    data = image.read(images.MAX_BYTES + 1)
    if len(data) > images.MAX_BYTES:
        return 413, {"detail": images.TOO_BIG}
    kind = images.kind_of(data)
    if not kind:
        return 415, {"detail": images.NOT_A_PICTURE}
    text = (message or '').strip()[:4000]
    first = not session.messages.exists()
    m = SupportMessage.objects.create(session=session, sender='visitor', message=text,
                                      image=data, image_type=kind)
    if not images.to_telegram(data, kind, images.caption_for(session, text, first)):
        logger.warning('support picture saved but not sent to telegram: session=%s', session.id)
    return 200, {"message": "Picture sent", "id": str(m.id), "image_url": images.url_of(m)}


@router.get("/chat/{session_id}/image/{message_id}", auth=None)
def get_image(request, session_id: str, message_id: str):
    """CH-654: one picture of a chat. Like the transcript, the session's own
    id is the key; the picture is sent as what it was checked to be, never
    run as a page."""
    from django.http import HttpResponse
    from support.models import SupportMessage
    if not (_uuid(session_id) and _uuid(message_id)):
        return HttpResponse(status=404)
    m = (SupportMessage.objects.filter(id=message_id, session_id=session_id)
         .exclude(image_type='').only('image', 'image_type').first())
    if m is None or m.image is None:
        return HttpResponse(status=404)
    response = HttpResponse(bytes(m.image), content_type=m.image_type)
    response['Content-Disposition'] = 'inline'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, max-age=86400'
    response['Content-Security-Policy'] = "default-src 'none'"
    return response


@router.post("/chat/{session_id}/webhook", auth=None, response={200: dict, 403: dict})
def telegram_webhook(request, session_id: str):
    import json
    from django.conf import settings
    secret = settings.SUPPORT_WEBHOOK_SECRET
    provided = request.headers.get('X-Support-Secret', '')
    if not secret or not provided or provided != secret:
        return 403, {"detail": "forbidden"}
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
    except Exception:
        logger.exception("support telegram_webhook failed")
        return 200, {"message": "error"}
