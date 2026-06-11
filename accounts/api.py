# accounts/api.py

from ninja import Router, Form
from ninja.security import HttpBearer
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from typing import Optional
from .schemas import *
from .services import (
    AuthService, ProfileService, MFAService, 
    PasswordResetService, APIKeyService, OrganizationService
)
from .models import User, ActivityLog
from ninja.security import HttpBearer

class JWTAuth(HttpBearer):
    def authenticate(self, request, token):
        from rest_framework_simplejwt.tokens import AccessToken
        from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
        from .models import User
        try:
            validated = AccessToken(token)
            user = User.objects.get(id=validated['user_id'])
            return user
        except (InvalidToken, TokenError, User.DoesNotExist):
            return None

router = Router(tags=["Authentication & Accounts"], auth=JWTAuth())
# Helper to get client IP
def get_client_ip(request):
    x_forwarded = request.headers.get('X-Forwarded-For')
    if x_forwarded:
        return x_forwarded.split(',')[0]
    return request.META.get('REMOTE_ADDR')


def get_user_agent(request):
    return request.META.get('HTTP_USER_AGENT', '')


# Custom authentication class for API key
class APIKeyAuth(HttpBearer):
    def authenticate(self, request, token):
        from .services import APIKeyService
        api_key = APIKeyService.validate(token)
        if api_key:
            request.auth_user = api_key.user
            request.auth_org = api_key.organization
            return api_key.user
        return None


# ========== AUTHENTICATION ENDPOINTS ==========

@router.post("/register", response={201: UserOutSchema, 400: dict}, auth=None)
def register(request: HttpRequest, data: RegisterSchema):
    """Register a new user and organization"""
    try:
        user = AuthService.register(
            data, 
            get_client_ip(request), 
            get_user_agent(request)
        )
        return 201, ProfileService.get_profile(user)
    except ValueError as e:
        return 400, {"detail": str(e)}

@router.post("/login", response={200: dict, 400: dict, 429: dict}, auth=None)
def login(request: HttpRequest, data: LoginSchema):
    from django_ratelimit.decorators import is_ratelimited
    if is_ratelimited(request, group="login", key="ip", rate="5/m", method="POST", increment=True):
        return 429, {"detail": "Too many login attempts. Please wait 1 minute."}
    try:
        result, is_mfa = AuthService.login(
            data.email,
            data.password,
            get_client_ip(request),
            get_user_agent(request)
        )
        return 200, result
    except ValueError as e:
        return 400, {"detail": str(e)}

@router.post("/verify-mfa", response={200: TokenResponseSchema, 400: dict}, auth=None)
def verify_mfa(request: HttpRequest, data: VerifyMFASchema):
    try:
        tokens = AuthService.verify_mfa(data.temp_token, data.token, get_client_ip(request), get_user_agent(request))
        return 200, tokens
    except ValueError as e:
        return 400, {"detail": str(e)}

@router.post("/logout", response={200: MessageResponseSchema})
def logout(request: HttpRequest):
    AuthService.logout(
        request.auth,
        get_client_ip(request),
        get_user_agent(request)
    )
    return {"message": "Logged out successfully", "success": True}

@router.post("/refresh", response={200: dict, 401: dict}, auth=None)
def refresh_token(request: HttpRequest, data: RefreshTokenSchema):
    from rest_framework_simplejwt.tokens import RefreshToken as JWTRefreshToken
    from rest_framework_simplejwt.exceptions import TokenError
    try:
        refresh = JWTRefreshToken(data.refresh)
        return 200, {"access": str(refresh.access_token)}
    except TokenError:
        return 401, {"detail": "Invalid refresh token"}


# ========== PROFILE ENDPOINTS ==========

@router.get("/me", response=UserOutSchema)
def get_profile(request: HttpRequest):
    """Get current user profile"""
    return ProfileService.get_profile(request.auth)


@router.post("/me/avatar", response={200: dict, 400: dict})
def upload_avatar(request: HttpRequest):
    import base64
    from django.core.files.base import ContentFile
    import uuid
    try:
        file = request.FILES.get('avatar')
        if not file:
            return 400, {"detail": "No file provided"}
        ext = file.name.split('.')[-1]
        filename = f"avatars/{uuid.uuid4()}.{ext}"
        user = request.auth
        user.avatar = filename
        user.save()
        return 200, {"avatar_url": filename, "message": "Avatar uploaded"}
    except Exception as e:
        return 400, {"detail": str(e)}


@router.patch("/me", response=UserOutSchema)
def update_profile(request: HttpRequest, data: UpdateProfileSchema):
    """Update current user profile"""
    user = ProfileService.update_profile(request.auth, data)
    return ProfileService.get_profile(user)


# ========== PASSWORD RESET ENDPOINTS ==========

@router.post("/password-reset/request", response=MessageResponseSchema, auth=None)
def request_password_reset(request: HttpRequest, data: PasswordResetRequestSchema):
    """Request password reset email"""
    PasswordResetService.request_reset(data.email)
    # Always return success for security (don't reveal if email exists)
    return {
        "message": "If an account exists with that email, a reset link has been sent",
        "success": True
    }


@router.post("/password-reset/confirm", response=MessageResponseSchema, auth=None)
def confirm_password_reset(request: HttpRequest, data: PasswordResetConfirmSchema):
    """Confirm password reset with token"""
    try:
        PasswordResetService.confirm_reset(data.token, data.new_password)
        return {"message": "Password reset successfully", "success": True}
    except ValueError as e:
        return 400, {"message": str(e), "success": False}


# ========== MFA ENDPOINTS ==========

@router.post("/mfa/setup", response=MFASetupResponseSchema)
def mfa_setup(request: HttpRequest):
    """Setup MFA for the current user"""
    return MFAService.generate_setup(request.auth)


@router.post("/mfa/verify", response={200: MessageResponseSchema, 400: dict})
def mfa_verify(request: HttpRequest, data: MFAVerifySchema):
    try:
        MFAService.verify_and_enable(request.auth, data.token)
        return {"message": "MFA enabled successfully", "success": True}
    except ValueError as e:
        return 400, {"message": str(e), "success": False}


@router.post("/mfa/disable", response={200: MessageResponseSchema, 400: dict})
def mfa_disable(request: HttpRequest, data: MFAVerifySchema):
    try:
        MFAService.disable(request.auth, data.token)
        return {"message": "MFA disabled successfully", "success": True}
    except ValueError as e:
        return 400, {"message": str(e), "success": False}


@router.post("/change-password", response={200: MessageResponseSchema, 400: dict})
def change_password(request: HttpRequest, data: ChangePasswordSchema):
    try:
        ProfileService.change_password(
            request.auth, 
            data.current_password, 
            data.new_password
        )
        return {"message": "Password changed successfully", "success": True}
    except ValueError as e:
        return 400, {"message": str(e), "success": False}

# ========== API KEY ENDPOINTS ==========

@router.get("/api-keys", response=list[APIKeyListSchema])
def list_api_keys(request: HttpRequest):
    """List all API keys for the current user"""
    keys = APIKeyService.list_keys(request.auth)
    return [
        {
            'id': str(k.id),
            'name': k.name,
            'key_prefix': k.key_prefix,
            'is_active': k.is_active,
            'last_used_at': k.last_used_at.isoformat() if k.last_used_at else None,
            'created_at': k.created_at.isoformat(),
            'expires_at': k.expires_at.isoformat() if k.expires_at else None
        }
        for k in keys
    ]


@router.post("/api-keys", response={201: APIKeyResponseSchema, 400: dict})
def create_api_key(request: HttpRequest, data: APIKeyCreateSchema):
    """Create a new API key"""
    from datetime import datetime
    
    expires_at = None
    if data.expires_at:
        expires_at = datetime.fromisoformat(data.expires_at)
    
    api_key = APIKeyService.create(request.auth, data.name, expires_at)
    return 201, api_key




@router.delete("/api-keys/{key_id}", response=MessageResponseSchema)
def revoke_api_key(request: HttpRequest, key_id: str):
    success = APIKeyService.revoke(key_id, request.auth)
    if success:
        return {"message": "API key revoked successfully", "success": True}
    return 404, {"message": "API key not found", "success": False}

@router.get("/activity", response=list[ActivityLogSchema])
def get_activity(request: HttpRequest):
    logs = ActivityLog.objects.filter(user=request.auth)[:50]
    return [
        {
            "id": str(l.id),
            "action": l.action,
            "ip_address": l.ip_address,
            "metadata": l.metadata,
            "created_at": l.created_at.isoformat()
        }
        for l in logs
    ]

# ─── Workspace ────────────────────────────────────────────────────────────────

@router.get("/workspace", response={200: dict})
def get_workspace(request: HttpRequest):
    org = request.auth.organization
    return 200, {
        'id': str(org.id),
        'name': org.name,
        'slug': org.slug,
        'is_active': org.is_active,
        'created_at': org.created_at.isoformat(),
    }


@router.patch("/workspace", response={200: dict, 400: dict})
def update_workspace(request: HttpRequest):
    import json
    org = request.auth.organization
    try:
        data = json.loads(request.body)
        if 'name' in data:
            org.name = data['name']
        if 'slug' in data:
            org.slug = data['slug']
        org.save()
        return 200, {
            'id': str(org.id),
            'name': org.name,
            'slug': org.slug,
            'is_active': org.is_active,
            'created_at': org.created_at.isoformat(),
        }
    except Exception as e:
        return 400, {"detail": str(e)}


@router.get("/workspace/members", response={200: dict})
def list_members(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    members = User.objects.filter(organization=request.auth.organization)
    data = [
        {
            'id': str(m.id),
            'email': m.email,
            'username': m.username,
            'first_name': m.first_name,
            'last_name': m.last_name,
            'role': m.role,
            'is_active': m.is_active,
            'status': 'active' if m.is_active else 'suspended',
            'created_at': m.created_at.isoformat(),
        }
        for m in members
    ]
    return 200, paginate_list(data, page, page_size)


@router.post("/workspace/members/invite", response={201: dict, 400: dict})
def invite_member(request: HttpRequest):
    import json
    import secrets
    try:
        data = json.loads(request.body)
        email = data.get('email')
        role = data.get('role', 'agent')
        if not email:
            return 400, {"detail": "Email is required"}
        if User.objects.filter(email=email).exists():
            return 400, {"detail": "User with this email already exists"}
        temp_password = secrets.token_urlsafe(12)
        user = User.objects.create(
            email=email,
            username=email.split('@')[0],
            role=role,
            organization=request.auth.organization,
            is_email_verified=True,
            is_active=True,
        )
        user.set_password(temp_password)
        user.save()
        return 201, {
            'id': str(user.id),
            'email': user.email,
            'role': user.role,
            'temp_password': temp_password,
            'message': 'Member invited successfully',
        }
    except Exception as e:
        return 400, {"detail": str(e)}


@router.delete("/workspace/members/{user_id}", response={200: dict, 404: dict})
def remove_member(request: HttpRequest, user_id: str):
    try:
        user = User.objects.get(id=user_id, organization=request.auth.organization)
        if user.id == request.auth.id:
            return 400, {"detail": "Cannot remove yourself"}
        user.delete()
        return 200, {"detail": "Member removed"}
    except User.DoesNotExist:
        return 404, {"detail": "Member not found"}


@router.patch("/workspace/members/{user_id}/role", response={200: dict, 400: dict, 404: dict})
def update_member_role(request: HttpRequest, user_id: str):
    import json
    try:
        data = json.loads(request.body)
        role = data.get('role')
        status = data.get('status')
        user = User.objects.get(id=user_id, organization=request.auth.organization)
        if role:
            user.role = role
        if status == 'suspended':
            user.is_active = False
        elif status == 'active':
            user.is_active = True
        user.save()
        return 200, {
            'id': str(user.id),
            'email': user.email,
            'role': user.role,
            'status': 'active' if user.is_active else 'suspended',
        }
    except User.DoesNotExist:
        return 404, {"detail": "Member not found"}
