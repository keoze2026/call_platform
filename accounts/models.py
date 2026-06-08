# accounts/models.py

import uuid
import pyotp
from django.contrib.auth.models import AbstractUser
from django.db import models


class Organization(models.Model):
    """Multi-tenant organization/account holder"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'organizations'
        ordering = ['name']
    
    def __str__(self):
        return self.name


class User(AbstractUser):
    """Extended user model with roles and MFA support"""
    
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        PUBLISHER = 'publisher', 'Publisher'
        BUYER = 'buyer', 'Buyer'
        AGENT = 'agent', 'Agent'
        RESELLER = 'reseller', 'Network/Reseller'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, 
        on_delete=models.CASCADE,
        related_name='users',
        null=True,
        blank=True
    )
    role = models.CharField(
        max_length=20, 
        choices=Role.choices, 
        default=Role.AGENT
    )
    phone_number = models.CharField(max_length=20, blank=True)
    
    # Email verification
    is_email_verified = models.BooleanField(default=False)
    email_verification_token = models.CharField(max_length=255, blank=True)
    
    # MFA (Multi-Factor Authentication)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.CharField(max_length=64, blank=True)
    
    # Tracking
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'users'
        ordering = ['-created_at']
    
    def get_mfa_totp(self):
        """Get TOTP object for MFA verification"""
        return pyotp.TOTP(self.mfa_secret)
    
    def verify_mfa_token(self, token: str) -> bool:
        """Verify MFA token"""
        if not self.mfa_enabled or not self.mfa_secret:
            return False
        return self.get_mfa_totp().verify(token, valid_window=1)
    
    def __str__(self):
        return f"{self.email} ({self.get_role_display()})"


class ActivityLog(models.Model):
    """Audit log for all user actions"""
    
    class Action(models.TextChoices):
        LOGIN = 'login', 'Login'
        LOGOUT = 'logout', 'Logout'
        PASSWORD_CHANGE = 'password_change', 'Password Change'
        MFA_ENABLED = 'mfa_enabled', 'MFA Enabled'
        MFA_DISABLED = 'mfa_disabled', 'MFA Disabled'
        API_KEY_CREATED = 'api_key_created', 'API Key Created'
        API_KEY_REVOKED = 'api_key_revoked', 'API Key Revoked'
        PROFILE_UPDATED = 'profile_updated', 'Profile Updated'
        ORGANIZATION_CREATED = 'organization_created', 'Organization Created'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='activity_logs'
    )
    action = models.CharField(max_length=50, choices=Action.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'activity_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.user.email} - {self.action} - {self.created_at}"


class APIKey(models.Model):
    """API keys for programmatic access"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='api_keys'
    )
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=12)  # Shown in UI (e.g., "sk_live_ab12...")
    key_hash = models.CharField(max_length=128)   # Stored hash, never the raw key
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'api_keys'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} ({self.key_prefix}...)"


class PasswordResetToken(models.Model):
    """Password reset tokens with expiration"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='reset_tokens'
    )
    token_hash = models.CharField(max_length=128)
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'password_reset_tokens'
    
    def is_valid(self):
        """Check if token is still valid (not used and not expired)"""
        from django.utils import timezone
        return not self.is_used and self.expires_at > timezone.now()
    
    def __str__(self):
        return f"Reset token for {self.user.email} - {'Valid' if self.is_valid() else 'Invalid'}"
from .kyc import KYCVerification
