from ninja import Router
from django.http import HttpRequest
import json
import logging

router = Router(tags=["Telegram Webhook"])

logger = logging.getLogger(__name__)


# The /help text, provided verbatim by the bot builder (DFGHE, 12 Oct) and
# sent with Telegram HTML parse mode.
HELP_TEXT = (
    "<b>AVORTYX BOT \u00b7 HELP</b>\n\n"
    "<b>LINK YOUR ACCOUNT</b>\n"
    "1. Open the Avortyx portal \u2192 <b>Settings</b>\n"
    "2. Generate your Telegram link\n"
    "3. Tap the link, or send:\n"
    "<code>/start yourcode</code>\n\n"
    "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
    "<b>SENT TO YOU AUTOMATICALLY</b>\n\n"
    "<b>Morning report</b>\n"
    "Calls, connections, billing, margin and balance\n\n"
    "<b>Down alerts</b>\n"
    "When calls stop arriving, with an <b>all clear</b> once they're back\n\n"
    "<b>Platform alerts</b>\n"
    "Buyers hitting caps, low balance and similar warnings\n\n"
    "<b>Support chats</b>\n"
    "Messages from customers on your portal\n\n"
    "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
    "<b>COMMANDS</b>\n"
    "/status \u2014 every service with its status (team only)\n"
    "/balance 1267.41 \u2014 update the carrier balance (team only)\n"
    "/id \u2014 this chat's ID\n\n"
    "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
    "<b>REPLY TO A SUPPORT CHAT</b>\n"
    "Send the session ID, a space, then your message:\n\n"
    "<code>7xxxxxxx Thanks, let me look into that for you</code>\n\n"
    "Your reply goes straight into the customer's chat."
)

WELCOME_TEXT = (
    "\U0001F44B <b>Welcome to Avortyx Bot</b>\n\n"
    "To get started, link your Avortyx account:\n\n"
    "1. Log in to the Avortyx portal\n"
    "2. Go to <b>Settings</b> and generate your Telegram link\n"
    "3. Tap the link, or send <code>/start yourcode</code>\n\n"
    "Once linked, everything arrives here automatically. "
    "There's nothing else to set up. Send /help any time."
)

LINKED_TEXT = (
    "\u2705 <b>Linked!</b> Your Avortyx account is connected. "
    "Reports and alerts arrive here automatically \u2014 send /help to see everything I do."
)


def _reply(chat_id: str, html: str) -> None:
    """Send one HTML message back to the chat. A failure is logged, never raised
    - the webhook must answer 200 whatever happens."""
    from django.conf import settings
    import requests
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not (token and chat_id):
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': html, 'parse_mode': 'HTML'},
            timeout=6,
        )
    except Exception:
        logger.exception('telegram reply to %s failed', chat_id)


REPORT_CONNECTED_TEXT = (
    "\u2705 <b>Connected</b>\n\n"
    "Daily call reports for <b>{buyer}</b> will be sent to this chat: a short summary "
    "and one Excel file per destination.\n\n"
    "They start once the report is switched on and approved in the Avortyx portal."
)

REPORT_LINK_EXPIRED_TEXT = (
    "This connect link has expired or was already used. "
    "Please ask for a new one from the Avortyx portal."
)

ADDED_TO_GROUP_TEXT = (
    "\U0001F44B <b>Avortyx Bot added</b>\n\n"
    "To receive a buyer's daily report in this group, open the connect link from the "
    "Avortyx portal. This group's chat ID is <code>{chat_id}</code>."
)


def _command(word: str) -> str:
    """'/start@AVSPTBOT' -> '/start': in a group Telegram adds the bot's name."""
    return word.split('@', 1)[0].lower()


def _handle_report_connect(chat_id: str, payload: str) -> bool:
    """CH-310: '/start rpt_<code>' connects a buyer's daily report to this
    chat (private, or a group the bot was added to with the link)."""
    from buyers.telegram_connect import PREFIX, connect
    if not payload.startswith(PREFIX):
        return False
    buyer = connect(payload, chat_id)
    if buyer is None:
        logger.warning('telegram report connect with unknown or used code from chat %s', chat_id)
        _reply(chat_id, REPORT_LINK_EXPIRED_TEXT)
    else:
        from html import escape
        _reply(chat_id, REPORT_CONNECTED_TEXT.format(buyer=escape(buyer.name)))
    return True


def _handle_status(chat_id: str, message: dict) -> None:
    """/status: answered at once, the full check follows in a few seconds
    (run by a worker, or here if the worker queue is not reachable)."""
    from support import system_status as S
    if not S.allowed_chat(chat_id):
        logger.warning('telegram /status refused for chat %s', chat_id)
        return
    thread = str(message.get('message_thread_id', '') or '')
    # CH-332: the /status itself goes from the group with the answer, after a week.
    from support.telegram_autodelete import remember_incoming
    remember_incoming(chat_id, message.get('message_id'))
    S.post('⏳ Checking every service…', chat=chat_id, thread=thread)
    try:
        from tasks import send_status_report
        send_status_report.delay(chat_id, thread)
    except Exception:
        import threading

        def run():
            S.post(S.render(S.collect()), chat=chat_id, thread=thread)
        threading.Thread(target=run, daemon=True).start()


def _handle_balance(chat_id: str, text: str) -> None:
    """/balance 1267.41 [daily spend]: the carrier balance the status bot
    watches (the carrier has no API for it). The team's chats only."""
    from support import system_status as S
    if not S.allowed_chat(chat_id):
        return
    parts = text.split()
    try:
        amount = float(parts[1].replace('$', '').replace(',', ''))
        spend = float(parts[2].replace('$', '').replace(',', '')) if len(parts) > 2 else None
    except (IndexError, ValueError):
        _reply(chat_id, 'Send it like: <code>/balance 1267.41</code> or <code>/balance 1267.41 35</code> '
                        '(amount, then average spend per day)')
        return
    b = S.set_balance(amount, spend)
    _reply(chat_id, f"✅ Carrier balance set to <b>${b['amount']:,.2f}</b>"
                    + (f", spend ${b['daily_spend']:,.0f}/day" if b.get('daily_spend') else '')
                    + '. I will warn before it runs low.')


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
    if _command(parts[0]) != '/start':
        return False

    chat_id = str(message.get('chat', {}).get('id', '') or '')
    handle = (message.get('from', {}).get('username', '') or '')

    if len(parts) == 2 and _handle_report_connect(chat_id, parts[1].strip()):
        return True

    if len(parts) < 2:
        logger.info('telegram /start with no code from chat %s', chat_id)
        _reply(chat_id, WELCOME_TEXT)
        return True

    code = parts[1].strip()
    link = TelegramLinkCode.objects.select_related('user').filter(code=code).first()

    if link is None:
        logger.warning('telegram /start with unknown code from chat %s', chat_id)
        _reply(chat_id, WELCOME_TEXT)
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
    _reply(chat_id, LINKED_TEXT)
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
        # CH-310: the bot was added to a group - say hello and give its chat ID.
        member = data.get('my_chat_member') or {}
        if member:
            chat = member.get('chat', {}) or {}
            status = (member.get('new_chat_member', {}) or {}).get('status', '')
            old = (member.get('old_chat_member', {}) or {}).get('status', '')
            if chat.get('type') in ('group', 'supergroup') and status in ('member', 'administrator') \
                    and old in ('left', 'kicked', ''):
                _reply(str(chat.get('id', '')), ADDED_TO_GROUP_TEXT.format(chat_id=chat.get('id', '')))
            return 200, {"ok": True}

        message = data.get('message', {}) or {}
        text = (message.get('text') or '').strip()

        if not text:
            return 200, {"ok": True}

        chat_id = str(message.get('chat', {}).get('id', '') or '')
        if _command(text.split()[0]) == '/help':
            _reply(chat_id, HELP_TEXT)
            return 200, {"ok": True}

        # CH-314: every service with its status. The team's chats only.
        if _command(text.split()[0]) == '/status':
            _handle_status(chat_id, message)
            return 200, {"ok": True}

        if _command(text.split()[0]) == '/balance':
            _handle_balance(chat_id, text)
            return 200, {"ok": True}

        # CH-310: our own "what is my chat ID" - no public bot needed.
        if _command(text.split()[0]) == '/id':
            _reply(chat_id, f"This chat's ID is <code>{chat_id}</code>")
            return 200, {"ok": True}

        if _handle_start(message, text):
            return 200, {"ok": True}

        _handle_support_reply(text)
        return 200, {"ok": True}

    except Exception:
        logger.exception('telegram webhook failed')
        return 200, {"ok": False}
