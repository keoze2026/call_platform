import secrets
import hashlib
import base64
import pyotp
import qrcode
from io import BytesIO
from datetime import datetime, timedelta
from django.utils import timezone
from django.contrib.auth import authenticate
from django.utils.text import slugify
from django.core.cache import cache
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User, Organization, ActivityLog, APIKey, PasswordResetToken
from .schemas import RegisterSchema
from .broadcast import send_activity_to_user


class AuthService:
    """Authentication and user management service"""
    
    @staticmethod
    def register(data: RegisterSchema, ip_address: str, user_agent: str) -> User:
        """Register a new user and their organization"""
        
        # Check if email exists
        if User.objects.filter(email=data.email).exists():
            raise ValueError("Email already registered")
        
        # Create organization
        org_name = data.organization_name
        base_slug = slugify(org_name)
        slug = base_slug
        counter = 1
        while Organization.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        organization = Organization.objects.create(
            name=org_name,
            slug=slug,
            is_active=True
        )
        
        # Create user (first user of org is Admin)
        user = User.objects.create_user(
            username=data.email,
            email=data.email,
            password=data.password,
            first_name=data.first_name,
            last_name=data.last_name,
            organization=organization,
            role=User.Role.ADMIN,
            is_active=True
        )
        
        # Log activity
        ActivityLog.objects.create(
            user=user,
            action=ActivityLog.Action.ORGANIZATION_CREATED,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={'organization_name': org_name}
        )
        
        return user
   
   
    @staticmethod
    def login(email: str, password: str, ip_address: str = None, user_agent: str = None):
        from accounts.models import User as UserModel
        try:
            u = UserModel.objects.get(email=email)
            user = authenticate(username=u.username, password=password)
        except UserModel.DoesNotExist:
            user = authenticate(username=email, password=password)

        if not user:
            raise ValueError("Invalid email or password")

        if not user.is_active:
            raise ValueError("Account is disabled. Please contact support")

        if user.mfa_enabled:
            temp_token = secrets.token_urlsafe(32)
            cache.set(f"mfa_{temp_token}", str(user.id), timeout=300)
            return {"mfa_required": True, "temp_token": temp_token}, True

        user.last_login_ip = ip_address
        user.last_login = timezone.now()
        user.save(update_fields=['last_login_ip', 'last_login'])

        ActivityLog.objects.create(
            user=user,
            action=ActivityLog.Action.LOGIN,
            ip_address=ip_address,
            user_agent=user_agent or ''
        )

        refresh = RefreshToken.for_user(user)

        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user_id': str(user.id),
            'email': user.email,
            'role': user.role,
            'organization_id': str(user.organization_id) if user.organization_id else None,
        }, False
    
    
    @staticmethod
    def verify_mfa(temp_token: str, mfa_code: str, ip_address: str, user_agent: str):
        from django.core.cache import cache
        
        user_id = cache.get(f"mfa_{temp_token}")
        if not user_id:
            raise ValueError("Invalid or expired MFA session")
        
        user = User.objects.get(id=user_id)
        
        if not user.verify_mfa_token(mfa_code):
            raise ValueError("Invalid MFA code")
        
        cache.delete(f"mfa_{temp_token}")
        
        user.last_login_ip = ip_address
        user.last_login = timezone.now()
        user.save(update_fields=['last_login_ip', 'last_login'])
        
        ActivityLog.objects.create(
            user=user,
            action=ActivityLog.Action.LOGIN,
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        refresh = RefreshToken.for_user(user)
        
        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user_id': str(user.id),
            'email': user.email,
            'role': user.role,
            'organization_id': str(user.organization_id) if user.organization_id else None,
        }
    
    @staticmethod
    def logout(user: User, ip_address: str, user_agent: str):
        """Log user out"""
        ActivityLog.objects.create(
            user=user,
            action=ActivityLog.Action.LOGOUT,
            ip_address=ip_address,
            user_agent=user_agent
        )


class ProfileService:
    """User profile management"""
    
    @staticmethod
    def get_profile(user: User) -> dict:
        """Get user profile with organization details"""
        return {
            'id': str(user.id),
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'role': user.role,
            'phone_number': user.phone_number,
            'mfa_enabled': user.mfa_enabled,
            'is_email_verified': user.is_email_verified,
            'organization_id': str(user.organization_id) if user.organization_id else None,
            'organization_name': user.organization.name if user.organization else None,
            'created_at': user.created_at.isoformat()
        }
    
    @staticmethod
    def update_profile(user: User, data) -> User:
        ALLOWED_FIELDS = {'first_name', 'last_name', 'phone_number'}

        for field, value in data.model_dump(exclude_none=True).items():
            if field in ALLOWED_FIELDS:
                setattr(user, field, value)

        user.save()

        ActivityLog.objects.create(
            user=user,
            action=ActivityLog.Action.PROFILE_UPDATED
        )

        return user
    
    @staticmethod
    def change_password(user: User, current_password: str, new_password: str):
        """Change user password"""
        if not user.check_password(current_password):
            raise ValueError("Current password is incorrect")
        
        user.set_password(new_password)
        user.save()
        
        ActivityLog.objects.create(
            user=user,
            action=ActivityLog.Action.PASSWORD_CHANGE
        )


class MFAService:
    """Multi-Factor Authentication service"""
    
    @staticmethod
    def generate_setup(user: User) -> dict:
        """Generate MFA secret and QR code"""
        import pyotp
        
        # Generate secret
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        
        # Generate provisioning URI
        issuer_name = "CallPlatform"
        qr_uri = totp.provisioning_uri(name=user.email, issuer_name=issuer_name)
        
        # Generate QR code as base64
        qr = qrcode.QRCode(box_size=10, border=4)
        qr.add_data(qr_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        qr_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        # Store secret temporarily (not enabled yet)
        user.mfa_secret = secret
        user.save(update_fields=['mfa_secret'])
        
        return {
            'secret': secret,
            'qr_uri': qr_uri,
            'qr_code_base64': qr_base64
        }
    
    @staticmethod
    def verify_and_enable(user: User, token: str) -> bool:
        if not user.mfa_secret:
            raise ValueError("No MFA setup in progress")

        totp = pyotp.TOTP(user.mfa_secret)
        print(f"Expected: {totp.now()} | Got: {token}")
        if not totp.verify(token, valid_window=1):
            raise ValueError("Invalid MFA token")

        user.mfa_enabled = True
        user.save(update_fields=['mfa_enabled'])

        ActivityLog.objects.create(
            user=user,
            action=ActivityLog.Action.MFA_ENABLED
        )

        return True
    
    @staticmethod
    def disable(user: User, token: str) -> bool:
        """Disable MFA for user"""
        if user.mfa_enabled and not user.verify_mfa_token(token):
            raise ValueError("Invalid MFA token")
        
        user.mfa_enabled = False
        user.mfa_secret = ''
        user.save(update_fields=['mfa_enabled', 'mfa_secret'])
        
        ActivityLog.objects.create(
            user=user,
            action=ActivityLog.Action.MFA_DISABLED
        )
        
        return True


class PasswordResetService:
    """Password reset functionality"""
    
    @staticmethod
    def request_reset(email: str):
        """Send password reset email (silent on non-existent email)"""
        from django.utils import timezone
        
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # Silent fail for security - don't reveal if email exists
            return
        
        # Generate reset token
        raw_token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        
        # Delete old unused tokens
        PasswordResetToken.objects.filter(user=user, is_used=False).delete()
        
        # Create new token
        PasswordResetToken.objects.create(
            user=user,
            token_hash=token_hash,
            expires_at=timezone.now() + timedelta(hours=24)
        )
        
        # TODO: Send email with reset link
        reset_url = f"https://app.callplatform.com/reset-password?token={raw_token}"
        print(f"Password reset email would be sent to {email}: {reset_url}")
        
        return
    
    @staticmethod
    def confirm_reset(raw_token: str, new_password: str) -> User:
        """Confirm password reset with token"""
        from django.utils import timezone
        
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        
        try:
            reset_token = PasswordResetToken.objects.get(
                token_hash=token_hash,
                is_used=False,
                expires_at__gt=timezone.now()
            )
        except PasswordResetToken.DoesNotExist:
            raise ValueError("Invalid or expired reset token")
        
        # Update password
        user = reset_token.user
        user.set_password(new_password)
        user.save()
        
        # Mark token as used
        reset_token.is_used = True
        reset_token.save()
        
        # Log activity
        ActivityLog.objects.create(
            user=user,
            action=ActivityLog.Action.PASSWORD_CHANGE
        )
        
        return user


class APIKeyService:
    """API key management"""
    
    @staticmethod
    def create(user: User, name: str, expires_at: datetime = None) -> dict:
        """Create a new API key for the user"""
        import hashlib
        import secrets
        
        # Generate raw key
        raw_key = f"sk_live_{secrets.token_urlsafe(32)}"
        key_prefix = raw_key[:12]
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        
        # Create API key record
        api_key = APIKey.objects.create(
            user=user,
            organization=user.organization,
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            expires_at=expires_at
        )
        
        # Log creation
        ActivityLog.objects.create(
            user=user,
            action=ActivityLog.Action.API_KEY_CREATED,
            metadata={'api_key_name': name}
        )
        
        return {
            'id': str(api_key.id),
            'name': api_key.name,
            'key': raw_key,  # Only returned once
            'key_prefix': key_prefix,
            'created_at': api_key.created_at.isoformat(),
            'expires_at': api_key.expires_at.isoformat() if api_key.expires_at else None
        }
    
    @staticmethod
    def validate(raw_key: str) -> APIKey | None:
        """Validate an API key and return the APIKey object if valid"""
        import hashlib
        from django.utils import timezone
        
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        
        try:
            api_key = APIKey.objects.select_related('user', 'organization').get(
                key_hash=key_hash,
                is_active=True
            )
            
            # Check expiration
            if api_key.expires_at and api_key.expires_at < timezone.now():
                return None
            
            # Update last used timestamp
            api_key.last_used_at = timezone.now()
            api_key.save(update_fields=['last_used_at'])
            
            return api_key
        except APIKey.DoesNotExist:
            return None
    
    @staticmethod
    def list_keys(user: User):
        """List all active API keys for a user"""
        return APIKey.objects.filter(user=user, is_active=True)
    
    @staticmethod
    def revoke(key_id: str, user: User):
        """Revoke an API key"""
        try:
            api_key = APIKey.objects.get(id=key_id, user=user)
            api_key.is_active = False
            api_key.save()
            
            ActivityLog.objects.create(
                user=user,
                action=ActivityLog.Action.API_KEY_REVOKED,
                metadata={'api_key_name': api_key.name}
            )
            
            return True
        except APIKey.DoesNotExist:
            return False


class OrganizationService:
    """Organization management"""
    
    @staticmethod
    def get_organization(organization_id: str):
        """Get organization by ID"""
        try:
            return Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist:
            return None
    
    @staticmethod
    def get_organization_users(organization: Organization):
        """Get all users in an organization"""
        return User.objects.filter(organization=organization, is_active=True)