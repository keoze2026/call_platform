"""CH-654: pictures in the support chat.

A customer can attach a picture to the portal's support chat - paste it while
typing, or pick a file - up to 2 MB. It is kept with the message (in the
database, so the nightly backup has it like the text) and goes to the support
team's Telegram group as a photo with the usual reply line. A team member who
answers with a photo (caption: the session's 8 characters, a space, the text)
puts that photo in the customer's chat the same way.

Only real pictures are taken: PNG, JPG, GIF and WEBP, recognised from the
file's own first bytes, never from its name or the type the browser claims.
Anything else (an SVG, a PDF, a renamed file) is refused.
"""
import html
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
TOO_BIG = 'The picture is larger than 2 MB.'
NOT_A_PICTURE = 'Only PNG, JPG, GIF or WEBP pictures can be sent.'


def kind_of(data: bytes) -> str:
    """The picture's type from its first bytes, or '' when it is not one we take."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return ''


def url_of(message) -> str | None:
    """Where the portal fetches a message's picture, or None."""
    if not message.image_type:
        return None
    return f'/api/support/chat/{message.session_id}/image/{message.id}'


def _thread():
    thread = getattr(settings, 'TELEGRAM_SUPPORT_THREAD_ID', '')
    return {'message_thread_id': int(thread)} if thread else {}


def to_telegram(data: bytes, kind: str, caption: str) -> bool:
    """Send the picture to the support group: as a photo, or as a file when
    Telegram will not take it as a photo (a GIF, an odd size). Never raises."""
    import requests
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(settings, 'TELEGRAM_SUPPORT_CHAT_ID', '')
    if not (token and chat_id):
        return False
    name = 'picture.' + kind.split('/')[-1].replace('jpeg', 'jpg')
    fields = {'chat_id': chat_id, 'caption': caption[:1024], 'parse_mode': 'HTML', **_thread()}
    attempts = [('sendDocument', 'document')] if kind == 'image/gif' else [('sendPhoto', 'photo'),
                                                                          ('sendDocument', 'document')]
    for method, field in attempts:
        try:
            r = requests.post(f'https://api.telegram.org/bot{token}/{method}', data=fields,
                              files={field: (name, data, kind)}, timeout=20)
            if r.ok and r.json().get('ok'):
                return True
            logger.warning('support picture: telegram %s refused: %s', method, r.text[:200])
        except Exception:
            logger.exception('support picture: telegram %s failed', method)
    return False


def caption_for(session, text: str, first: bool) -> str:
    name = html.escape(session.name or 'Anonymous')
    prefix = str(session.id)[:8]
    head = f'🖼 <b>{"New Support Chat" if first else "Picture"} from {name}</b>'
    body = f'\n\n<b>Message:</b> {html.escape(text)}' if text else ''
    return (f'{head}{body}\n\n─────────────────\n📨 <b>To reply, send:</b>\n'
            f'<code>{prefix} your reply here</code>')


def from_telegram(message: dict) -> tuple[bytes, str] | None:
    """A photo (or a picture sent as a file) in a Telegram message, downloaded:
    (bytes, type), or None when there is none we can take."""
    import requests
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return None
    file_id = None
    photos = [p for p in (message.get('photo') or []) if (p.get('file_size') or 0) <= MAX_BYTES]
    if photos:
        # Telegram sends the same photo in several sizes: the largest that fits.
        file_id = max(photos, key=lambda p: (p.get('file_size') or 0, p.get('width', 0) * p.get('height', 0)))['file_id']
    else:
        doc = message.get('document') or {}
        if str(doc.get('mime_type', '')).startswith('image/') and (doc.get('file_size') or 0) <= MAX_BYTES:
            file_id = doc.get('file_id')
    if not file_id:
        return None
    try:
        r = requests.get(f'https://api.telegram.org/bot{token}/getFile', params={'file_id': file_id}, timeout=10)
        path = r.json().get('result', {}).get('file_path')
        if not path:
            return None
        f = requests.get(f'https://api.telegram.org/file/bot{token}/{path}', timeout=20)
        data = f.content if f.ok else b''
    except Exception:
        logger.exception('support picture: download from telegram failed')
        return None
    kind = kind_of(data)
    if not kind or len(data) > MAX_BYTES:
        return None
    return data, kind
