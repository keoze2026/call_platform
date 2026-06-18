from ninja import Router
from django.http import HttpRequest
import json

router = Router(tags=["Telegram Webhook"])


@router.post("/telegram-webhook", auth=None, response={200: dict})
def telegram_webhook(request: HttpRequest):
    try:
        data = json.loads(request.body)
        message = data.get('message', {})
        text = message.get('text', '').strip()

        if not text:
            return 200, {"ok": True}

        # Format: first 8 chars of session ID + space + message
        parts = text.split(' ', 1)
        if len(parts) == 2 and len(parts[0]) == 8:
            session_prefix = parts[0]
            reply_text = parts[1]

            from support.models import SupportSession, SupportMessage
            session = SupportSession.objects.filter(id__startswith=session_prefix).first()
            if session:
                SupportMessage.objects.create(
                    session=session,
                    sender='agent',
                    message=reply_text,
                )
                print(f'Reply saved for session {session.id}')

        return 200, {"ok": True}
    except Exception as e:
        print('Webhook error:', e)
        return 200, {"ok": False}
