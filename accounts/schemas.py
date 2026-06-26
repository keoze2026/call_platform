# accounts/schemas.py

from ninja import Schema
from pydantic import EmailStr, field_validator
from typing import Optional, List
import re


# Request Schemas
class RegisterSchema(Schema):
    """User registration request"""
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    organization_name: str
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'\d', v):
            raise ValueError('Password must contain at least one number')
        return v


class LoginSchema(Schema):
    """User login request"""
    email: EmailStr
    password: str



class RefreshTokenSchema(Schema):
    """Refresh access token request"""
    refresh: str


class ChangePasswordSchema(Schema):
    """Change password request"""
    current_password: str
    new_password: str
    
    @field_validator('new_password')
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        return v


class PasswordResetRequestSchema(Schema):
    """Request password reset"""
    email: EmailStr


class PasswordResetConfirmSchema(Schema):
    """Confirm password reset"""
    token: str
    new_password: str
    
    @field_validator('new_password')
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        return v


class UpdateProfileSchema(Schema):
    """Update user profile"""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None


class MFAVerifySchema(Schema):
    """Verify MFA token"""
    token: str


class VerifyMFASchema(Schema):
    temp_token: str
    token: str

class APIKeyCreateSchema(Schema):
    """Create API key request"""
    name: str
    expires_at: Optional[str] = None  # ISO format date


class InviteUserSchema(Schema):
    """Invite a new user to organization"""
    email: EmailStr
    role: str  # publisher, buyer, agent, admin


# Response Schemas
class TokenResponseSchema(Schema):
    """Authentication token response"""
    access: str
    refresh: str
    user_id: str
    email: str
    role: str
    organization_id: Optional[str] = None
    mfa_required: bool = False


class UserOutSchema(Schema):
    """User profile response"""
    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    phone_number: str
    mfa_enabled: bool
    is_email_verified: bool
    organization_id: Optional[str] = None
    organization_name: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: str


class OrganizationOutSchema(Schema):
    """Organization response"""
    id: str
    name: str
    slug: str
    is_active: bool
    created_at: str


class MFASetupResponseSchema(Schema):
    """MFA setup response with QR code"""
    secret: str
    qr_uri: str
    qr_code_base64: str  # Base64 encoded QR code image


class APIKeyResponseSchema(Schema):
    """API key creation response (returns the raw key once)"""
    id: str
    name: str
    key: str  # Raw key - shown only once
    key_prefix: str
    created_at: str
    expires_at: Optional[str] = None


class APIKeyListSchema(Schema):
    """API key list response (without the raw key)"""
    id: str
    name: str
    key_prefix: str
    is_active: bool
    last_used_at: Optional[str] = None
    created_at: str
    expires_at: Optional[str] = None


class ActivityLogSchema(Schema):
    """Activity log entry"""
    id: str
    action: str
    ip_address: Optional[str]
    metadata: dict
    created_at: str


class MessageResponseSchema(Schema):
    """Simple message response"""
    message: str
    success: bool = True