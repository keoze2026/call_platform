from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import DNIPool, DNINumber, DNISession


class DNINumberInline(TabularInline):
    model = DNINumber
    extra = 0
    readonly_fields = ['id', 'created_at']


class DNISessionInline(TabularInline):
    model = DNISession
    extra = 0
    readonly_fields = [
        'id', 'visitor_id', 'ip_address', 'user_agent',
        'utm_source', 'utm_medium', 'utm_campaign',
        'utm_term', 'utm_content', 'gclid', 'fbclid',
        'referrer', 'landing_page', 'assigned_number',
        'status', 'expires_at', 'created_at', 'updated_at'
    ]


@admin.register(DNIPool)
class DNIPoolAdmin(ModelAdmin):
    list_display = [
        'name', 'organization', 'campaign', 'status',
        'session_duration_minutes', 'fallback_number', 'created_at'
    ]
    list_filter = ['status', 'organization', 'campaign']
    search_fields = ['name', 'description', 'campaign__name', 'fallback_number']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [DNINumberInline]
    fieldsets = (
        ('Pool Info', {
            'fields': (
                'id', 'organization', 'campaign',
                'name', 'description', 'status'
            )
        }),
        ('Settings', {
            'fields': (
                'session_duration_minutes',
                'fallback_number',
                'allowed_domains'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(DNINumber)
class DNINumberAdmin(ModelAdmin):
    list_display = [
        'number', 'pool', 'status', 'phone_number', 'created_at'
    ]
    list_filter = ['status', 'pool']
    search_fields = ['number', 'pool__name']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at']
    fieldsets = (
        ('Number Info', {
            'fields': ('id', 'pool', 'number', 'status', 'phone_number')
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )


@admin.register(DNISession)
class DNISessionAdmin(ModelAdmin):
    list_display = [
        'visitor_id', 'pool', 'dni_number', 'assigned_number',
        'status', 'utm_source', 'utm_medium', 'utm_campaign',
        'ip_address', 'expires_at', 'created_at'
    ]
    list_filter = ['status', 'pool', 'utm_source', 'utm_medium']
    search_fields = [
        'visitor_id', 'assigned_number', 'ip_address',
        'utm_source', 'utm_medium', 'utm_campaign',
        'utm_term', 'utm_content', 'gclid', 'fbclid',
        'referrer', 'landing_page'
    ]
    ordering = ['-created_at']
    readonly_fields = [
        'id', 'visitor_id', 'ip_address', 'user_agent',
        'utm_source', 'utm_medium', 'utm_campaign',
        'utm_term', 'utm_content', 'gclid', 'fbclid',
        'referrer', 'landing_page', 'assigned_number',
        'expires_at', 'created_at', 'updated_at'
    ]
    fieldsets = (
        ('Session Info', {
            'fields': (
                'id', 'pool', 'dni_number',
                'assigned_number', 'status', 'expires_at'
            )
        }),
        ('Visitor', {
            'fields': ('visitor_id', 'ip_address', 'user_agent')
        }),
        ('UTM Tracking', {
            'fields': (
                'utm_source', 'utm_medium', 'utm_campaign',
                'utm_term', 'utm_content', 'gclid', 'fbclid',
                'referrer', 'landing_page'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )