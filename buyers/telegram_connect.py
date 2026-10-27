"""Connect a buyer's daily report to Telegram through our own bot (CH-310).

Until now the buyer had to find its Telegram chat ID with a public bot
(@userinfobot) and send it to us to type in. Now the portal makes a connect
link instead:

    https://t.me/<our bot>?start=rpt_<code>        private chat with the bot
    https://t.me/<our bot>?startgroup=rpt_<code>   add the bot to a group

Telegram then sends our bot "/start rpt_<code>" from that chat (support/
telegram_webhook.py), and the chat is saved on the buyer's report - nothing
to copy. A code works once, within 7 days, for one buyer, and is kept only
in the cache (no table). Connecting a chat does not switch the report on and does
not approve it: the admin switches it on, the buyer approves it, as before.
"""
import logging
import secrets
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

PREFIX = 'rpt_'
LIFETIME = timedelta(days=7)
_KEY = 'tg_report_connect:{}'


def make_link(buyer) -> dict:
    """A new connect link for this buyer's report."""
    from buyers.telegram_report import bot_username
    code = secrets.token_urlsafe(12)
    cache.set(_KEY.format(code), str(buyer.id), int(LIFETIME.total_seconds()))
    bot = bot_username().lstrip('@')
    payload = PREFIX + code
    return {
        "bot": f'@{bot}' if bot else '',
        "url": f'https://t.me/{bot}?start={payload}' if bot else '',
        "group_url": f'https://t.me/{bot}?startgroup={payload}' if bot else '',
        "command": f'/start {payload}',
        "expires_at": (timezone.now() + LIFETIME).isoformat(),
    }


def connect(payload: str, chat_id: str):
    """Save `chat_id` on the report of the buyer the code belongs to.
    Returns the buyer, or None for an unknown or expired code."""
    from buyers.models import Buyer, BuyerTelegramReport
    code = payload[len(PREFIX):] if payload.startswith(PREFIX) else ''
    buyer_id = cache.get(_KEY.format(code)) if code else None
    if not buyer_id or not chat_id:
        return None
    buyer = Buyer.objects.filter(id=buyer_id).first()
    if buyer is None:
        return None
    # One use: a link forwarded on cannot later move the reports to another chat.
    cache.delete(_KEY.format(code))
    report, _ = BuyerTelegramReport.objects.get_or_create(buyer=buyer)
    report.chat_id = str(chat_id)[:64]
    report.save(update_fields=['chat_id', 'updated_at'])
    logger.info('telegram report of %s connected to chat %s', buyer.name, chat_id)
    return buyer
