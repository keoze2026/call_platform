from ninja import Router, Schema
from typing import Optional
from django.utils import timezone
from datetime import timedelta
from accounts.api import JWTAuth
import secrets

router = Router(tags=["Access Requests"])


class AccessRequestSchema(Schema):
    name: str
    company: str
    email: str
    phone: str = ''
    use_case: str = ''


class ApproveSchema(Schema):
    role: str = 'buyer'


class RejectSchema(Schema):
    reason: str = ''


class SetPasswordSchema(Schema):
    token: str
    password: str


def format_request(r):
    return {
        'id': str(r.id),
        'name': r.name,
        'company': r.company,
        'email': r.email,
        'phone': r.phone,
        'use_case': r.use_case,
        'status': r.status,
        'rejection_reason': r.rejection_reason,
        'reviewed_at': str(r.reviewed_at) if r.reviewed_at else None,
        'created_at': str(r.created_at),
    }


@router.post("/", auth=None, response={201: dict, 400: dict, 429: dict})
def create_access_request(request, payload: AccessRequestSchema):
    from django_ratelimit.decorators import is_ratelimited
    if is_ratelimited(request, group='access_request', key='ip', rate='5/m', method='POST', increment=True):
        return 429, {"detail": "Too many requests. Please wait 1 minute."}

    from accounts.access_requests import AccessRequest
    if AccessRequest.objects.filter(email=payload.email).exists():
        return 400, {"detail": "A request with this email already exists."}

    req = AccessRequest.objects.create(
        name=payload.name,
        company=payload.company,
        email=payload.email,
        phone=payload.phone,
        use_case=payload.use_case,
    )
    return 201, format_request(req)


@router.get("/", auth=JWTAuth(), response={200: dict})
def list_access_requests(request, status: Optional[str] = None, page: int = 1, page_size: int = 50):
    from accounts.access_requests import AccessRequest
    from config.pagination import paginate_list
    qs = AccessRequest.objects.all()
    if status:
        qs = qs.filter(status=status)
    else:
        qs = qs.filter(status='pending')
    data = [format_request(r) for r in qs]
    return 200, paginate_list(data, page, page_size)


@router.post("/{request_id}/approve/", auth=JWTAuth(), response={200: dict, 404: dict, 400: dict})
def approve_access_request(request, request_id: str, payload: ApproveSchema):
    from accounts.access_requests import AccessRequest, SetupToken
    from accounts.models import User, Organization

    try:
        req = AccessRequest.objects.get(id=request_id)
    except AccessRequest.DoesNotExist:
        return 404, {"detail": "Request not found"}

    if req.status == 'approved':
        return 200, {"detail": "Already approved", "user_id": str(req.reviewed_by_id) if req.reviewed_by_id else None}
    if req.status == 'rejected':
        return 400, {"detail": "Request was rejected. Cannot approve a rejected request."}

    # Create organization
    org = Organization.objects.create(
        name=req.company,
        slug=req.company.lower().replace(' ', '-')[:50],
    )

    # Create user with no password
    name_parts = req.name.split(' ', 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ''
    username = req.email.split('@')[0]

    user = User.objects.create(
        email=req.email,
        username=username,
        first_name=first_name,
        last_name=last_name,
        phone_number=req.phone,
        role=payload.role,
        organization=org,
        is_active=True,
        is_email_verified=True,
    )
    user.set_unusable_password()
    user.save()

    # Create setup token
    token_str = secrets.token_urlsafe(48)
    setup_token = SetupToken.objects.create(
        user=user,
        token=token_str,
        expires_at=timezone.now() + timedelta(hours=48),
    )

    # Send email
    setup_link = f"https://avortyx.com/set-password?token={token_str}"
    try:
        from django.core.mail import send_mail
        send_mail(
            subject="Welcome to Avortyx — Set Your Password",
            message=f"Hi {first_name},\n\nYour account has been approved. Click the link below to set your password and sign in:\n\n{setup_link}\n\nThis link expires in 48 hours.\n\nAvortyx Team",
            from_email="support@avortyx.io",
            recipient_list=[req.email],
            fail_silently=True,
        )
    except Exception:
        pass

    # Update request
    req.status = 'approved'
    req.reviewed_at = timezone.now()
    req.reviewed_by = request.auth
    req.save()

    return 200, {
        "detail": "Request approved",
        "user_id": str(user.id),
        "setup_link": setup_link,
        "email_field": "setup_link",
        "token_ttl_hours": 48,
        "email_sent": "synchronous",
    }


@router.post("/{request_id}/reject/", auth=JWTAuth(), response={200: dict, 404: dict})
def reject_access_request(request, request_id: str, payload: RejectSchema):
    from accounts.access_requests import AccessRequest
    try:
        req = AccessRequest.objects.get(id=request_id)
        req.status = 'rejected'
        req.rejection_reason = payload.reason
        req.reviewed_at = timezone.now()
        req.reviewed_by = request.auth
        req.save()
        return 200, {"detail": "Request rejected"}
    except AccessRequest.DoesNotExist:
        return 404, {"detail": "Request not found"}


@router.post("/set-password/", auth=None, response={200: dict, 400: dict})
def set_password(request, payload: SetPasswordSchema):
    from accounts.access_requests import SetupToken

    try:
        setup_token = SetupToken.objects.get(token=payload.token)
    except SetupToken.DoesNotExist:
        return 400, {"error": "token_invalid", "detail": "Invalid token"}

    if setup_token.is_used:
        return 400, {"error": "token_already_used", "detail": "This link has already been used"}

    if timezone.now() > setup_token.expires_at:
        return 400, {"error": "token_expired", "detail": "This link has expired. Please request a new one."}

    if len(payload.password) < 8:
        return 400, {"error": "password_too_weak", "detail": "Password must be at least 8 characters"}

    user = setup_token.user
    user.set_password(payload.password)
    user.save()

    setup_token.is_used = True
    setup_token.save()

    # Return same shape as login
    from rest_framework_simplejwt.tokens import RefreshToken
    refresh = RefreshToken.for_user(user)
    return 200, {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user_id": str(user.id),
        "email": user.email,
        "role": user.role,
        "organization_id": str(user.organization_id) if user.organization_id else None,
    }
