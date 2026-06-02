from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Blacklist, Whitelist, AnonymousCallBlock, SpamReport


@admin.register(Blacklist)
class BlacklistAdmin(ModelAdmin):
    list_display = [
        'phone_number', 'organization_id', 'campaign',
        'reason', 'is_active', 'expires_at', 'added_by', 'created_at'
    ]
    list_filter = ['reason', 'is_active', 'campaign']
    search_fields = ['phone_number', 'notes']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fieldsets = (
        ('Block Info', {
            'fields': (
                'id', 'organization_id', 'campaign',
                'phone_number', 'reason', 'notes',
                'is_active', 'expires_at', 'added_by'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(Whitelist)
class WhitelistAdmin(ModelAdmin):
    list_display = [
        'phone_number', 'organization_id', 'campaign',
        'is_active', 'added_by', 'created_at'
    ]
    list_filter = ['is_active', 'campaign']
    search_fields = ['phone_number', 'notes']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fieldsets = (
        ('Whitelist Info', {
            'fields': (
                'id', 'organization_id', 'campaign',
                'phone_number', 'notes',
                'is_active', 'added_by'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(AnonymousCallBlock)
class AnonymousCallBlockAdmin(ModelAdmin):
    list_display = ['campaign', 'organization_id', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['campaign__name']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fieldsets = (
        ('Block Info', {
            'fields': ('id', 'organization_id', 'campaign', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(SpamReport)
class SpamReportAdmin(ModelAdmin):
    list_display = [
        'phone_number', 'organization_id', 'campaign',
        'block_reason', 'twilio_call_sid', 'created_at'
    ]
    list_filter = ['block_reason', 'campaign']
    search_fields = ['phone_number', 'twilio_call_sid']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at']
    fieldsets = (
        ('Report Info', {
            'fields': (
                'id', 'organization_id', 'campaign',
                'phone_number', 'block_reason', 'twilio_call_sid'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )