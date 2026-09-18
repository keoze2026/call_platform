from ninja import Router
from django.http import HttpRequest
import json
import logging

router = Router(tags=["Telegram Webhook"])

logger = logging.getLogger(__name__)


def _handle_start(message: dict, text: str) -> bool:
    """Complete a profile link from '/start <code>'.

    Telegram delivers the deep-link payload as the argument to /start, which is
    the only point at which we learn the user's chat_id. Returns True if the
    update was a /start, whether or not the code turned out to be valid — the
    caller should not then treat it as a support reply.
    """
    from django.utils import timezone
    from accounts.models import TelegramLinkCode

    parts = text.split(maxsplit=1)
    if parts[0] != '/start':
        return False

    chat_id = str(message.get('chat', {}).get('id', '') or '')
    handle = (message.get('from', {}).get('username', '') or '')

    if len(parts) < 2:
        logger.info('telegram /start with no code from chat %s', chat_id)
        return True

    code = parts[1].strip()
    link = TelegramLinkCode.objects.select_related('user').filter(code=code).first()

    if link is None:
        logger.warning('telegram /start with unknown code from chat %s', chat_id)
        return True

    if not link.is_usable:
        logger.warning('telegram /start with spent or expired code for %s', link.user.email)
        return True

    if not chat_id:
        logger.warning('telegram /start carried no chat id for %s', link.user.email)
        return True

    user = link.user
    user.telegram_chat_id = chat_id
    fields = ['telegram_chat_id']
    # Only fill the handle in if the user has not set one themselves
    if handle and not user.telegram_username:
        user.telegram_username = handle[:64]
        fields.append('telegram_username')
    user.save(update_fields=fields)

    link.used_at = timezone.now()
    link.save(update_fields=['used_at'])

    logger.info('telegram linked chat %s to %s', chat_id, user.email)
    return True


def _handle_support_reply(text: str) -> None:
    """Agent replying to a support session: '<8-char session prefix> <message>'."""
    parts = text.split(' ', 1)
    if len(parts) != 2 or len(parts[0]) != 8:
        return

    from support.models import SupportSession, SupportMessage

    session = SupportSession.objects.filter(id__startswith=parts[0]).first()
    if session is None:
        return

    SupportMessage.objects.create(
        session=session,
        sender='agent',
        message=parts[1],
    )
    logger.info('support reply saved for session %s', session.id)


@router.post("/telegram-webhook", auth=None, response={200: dict})
def telegram_webhook(request: HttpRequest):
    # Always answer 200: a non-200 makes Telegram retry the same update forever.
    try:
        data = json.loads(request.body)
        message = data.get('message', {}) or {}
        text = (message.get('text') or '').strip()

        if not text:
            return 200, {"ok": True}

        if _handle_start(message, text):
            return 200, {"ok": True}

        _handle_support_reply(text)
        return 200, {"ok": True}

    except Exception:
        logger.exception('telegram webhook failed')
        return 200, {"ok": False}
