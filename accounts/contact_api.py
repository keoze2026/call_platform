from ninja import Router, Schema

router = Router(tags=["Contact"])


class ContactSchema(Schema):
    name: str
    email: str
    message: str


@router.post("/", auth=None, response={201: dict, 422: dict, 429: dict})
def contact(request, payload: ContactSchema):
    from django_ratelimit.decorators import is_ratelimited
    if is_ratelimited(request, group='contact', key='ip', rate='100/m', method='POST', increment=True):
        return 429, {"detail": "Too many requests. Please wait a minute."}

    name = payload.name.strip()
    email = payload.email.strip()
    message = payload.message.strip()

    errors = []
    if not name or len(name) > 120:
        errors.append({"loc": ["body", "name"], "msg": "Name is required"})
    if not email or '@' not in email:
        errors.append({"loc": ["body", "email"], "msg": "Valid email is required"})
    if not message or len(message) < 5 or len(message) > 4000:
        errors.append({"loc": ["body", "message"], "msg": "Message must be 5-4000 characters"})
    if errors:
        return 422, {"detail": errors}

    ip = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', '')
    ua = request.META.get('HTTP_USER_AGENT', '')

    from accounts.contact import ContactMessage
    msg = ContactMessage.objects.create(
        name=name,
        email=email,
        message=message,
        ip_address=ip or None,
        user_agent=ua,
    )

    # User confirmation
    try:
        from django.core.mail import send_mail
        send_mail(
            subject='We received your message — Avortyx',
            message=f'Hi {name},\n\nThank you for contacting us. We have received your message and will get back to you shortly.\n\nAvortyx Team',
            from_email='support@keozx.com',
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception as e:
        print('User email failed:', e)

    import time
    time.sleep(1)

    # Admin notification
    try:
        from django.core.mail import send_mail
        send_mail(
            subject=f'New contact form: {name}',
            message=f'Name: {name}\nEmail: {email}\n\nMessage:\n{message}\n\n---\nID: {msg.id}',
            from_email='support@keozx.com',
            recipient_list=['support@keozx.com'],
            fail_silently=False,
        )
    except Exception as e:
        print('Admin email failed:', e)

    time.sleep(1)

    # Telegram direct call
    try:
        from django.conf import settings
        from tasks import send_telegram
        bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '')
        if bot_token and chat_id:
            ellipsis = '...' if len(message) > 300 else ''
            tg_message = f'New contact form submission\nName: {name}\nEmail: {email}\nMessage: {message[:300]}{ellipsis}'
            send_telegram(bot_token, chat_id, tg_message)
    except Exception as e:
        print('Telegram task failed:', e)

    return 201, {
        'id': str(msg.id),
        'created_at': msg.created_at.isoformat(),
    }
