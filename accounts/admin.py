from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from unfold.admin import ModelAdmin
from .models import Organization, User


@admin.register(Organization)
class OrganizationAdmin(ModelAdmin):
    list_display = ['name', 'slug', 'is_active', 'created_at', 'updated_at']
    list_filter = ['is_active']
    search_fields = ['name', 'slug']
    ordering = ['name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fieldsets = (
        ('Organization Info', {'fields': ('id', 'name', 'slug', 'is_active')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )


@admin.register(User)
class CustomUserAdmin(UserAdmin, ModelAdmin):
    list_display = ['email', 'username', 'role', 'organization', 'is_email_verified', 'mfa_enabled', 'is_active', 'created_at']
    list_filter = ['role', 'is_active', 'is_email_verified', 'mfa_enabled', 'organization']
    search_fields = ['email', 'username', 'phone_number']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at', 'last_login_ip']
    fieldsets = (
        ('Account', {'fields': ('id', 'username', 'email', 'password')}),
        ('Personal', {'fields': ('first_name', 'last_name', 'phone_number')}),
        ('Organization & Role', {'fields': ('organization', 'role')}),
        ('Verification', {'fields': ('is_email_verified', 'email_verification_token')}),
        ('MFA', {'fields': ('mfa_enabled', 'mfa_secret')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Tracking', {'fields': ('last_login_ip', 'last_login', 'created_at', 'updated_at')}),
    )
    add_fieldsets = (
        ('Create User', {
            'classes': ('wide',),
            'fields': ('username', 'email', 'organization', 'role', 'password1', 'password2'),
        }),
    )