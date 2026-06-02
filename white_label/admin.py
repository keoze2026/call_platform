from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import WhiteLabel, WhiteLabelDomain


class WhiteLabelDomainInline(TabularInline):
    model = WhiteLabelDomain
    extra = 0
    readonly_fields = ['id', 'verified_at', 'created_at', 'updated_at']


@admin.register(WhiteLabel)
class WhiteLabelAdmin(ModelAdmin):
    list_display = [
        'company_name', 'organization', 'status',
        'support_email', 'support_phone',
        'website_url', 'created_at'
    ]
    list_filter = ['status', 'organization']
    search_fields = [
        'company_name', 'support_email',
        'support_phone', 'website_url',
        'organization__name'
    ]
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [WhiteLabelDomainInline]
    fieldsets = (
        ('Basic Info', {
            'fields': (
                'id', 'organization', 'created_by',
                'company_name', 'status'
            )
        }),
        ('Branding', {
            'fields': (
                'logo_url', 'favicon_url',
                'support_email', 'support_phone',
                'website_url'
            )
        }),
        ('Colors', {
            'fields': (
                'primary_color', 'secondary_color',
                'accent_color', 'background_color',
                'text_color'
            )
        }),
        ('Custom CSS', {
            'fields': ('custom_css',)
        }),
        ('Email Templates', {
            'fields': (
                'email_header', 'email_footer',
                'email_from_name', 'email_from_address'
            )
        }),
        ('Feature Flags', {
            'fields': ('features',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(WhiteLabelDomain)
class WhiteLabelDomainAdmin(ModelAdmin):
    list_display = [
        'domain', 'white_label', 'is_primary',
        'status', 'verified_at', 'created_at'
    ]
    list_filter = ['status', 'is_primary']
    search_fields = ['domain', 'white_label__company_name']
    ordering = ['-created_at']
    readonly_fields = ['id', 'verified_at', 'created_at', 'updated_at']
    fieldsets = (
        ('Domain Info', {
            'fields': (
                'id', 'white_label', 'domain',
                'is_primary', 'status', 'verified_at'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )