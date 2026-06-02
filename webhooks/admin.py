from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import Webhook, WebhookDelivery, ConversionPixel, ConversionEvent


class WebhookDeliveryInline(TabularInline):
    model = WebhookDelivery
    extra = 0
    readonly_fields = [
        'id', 'event', 'payload', 'status',
        'response_code', 'response_body',
        'attempts', 'next_retry_at',
        'created_at', 'updated_at'
    ]


class ConversionEventInline(TabularInline):
    model = ConversionEvent
    extra = 0
    readonly_fields = [
        'id', 'call_log', 'caller_number',
        'conversion_value', 'raw_payload',
        'source_ip', 'created_at'
    ]


@admin.register(Webhook)
class WebhookAdmin(ModelAdmin):
    list_display = [
        'name', 'organization', 'url', 'status',
        'max_retries', 'timeout_seconds', 'created_at'
    ]
    list_filter = ['status', 'organization']
    search_fields = ['name', 'url']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [WebhookDeliveryInline]
    fieldsets = (
        ('Webhook Info', {
            'fields': (
                'id', 'organization', 'created_by',
                'name', 'url', 'secret',
                'events', 'status'
            )
        }),
        ('Retry Settings', {
            'fields': ('max_retries', 'timeout_seconds')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(ModelAdmin):
    list_display = [
        'webhook', 'event', 'status',
        'response_code', 'attempts',
        'next_retry_at', 'created_at'
    ]
    list_filter = ['status', 'event', 'webhook']
    search_fields = ['event', 'webhook__name', 'response_body']
    ordering = ['-created_at']
    readonly_fields = [
        'id', 'webhook', 'event', 'payload',
        'status', 'response_code', 'response_body',
        'attempts', 'next_retry_at',
        'created_at', 'updated_at'
    ]
    fieldsets = (
        ('Delivery Info', {
            'fields': (
                'id', 'webhook', 'event', 'status',
                'response_code', 'attempts', 'next_retry_at'
            )
        }),
        ('Payload & Response', {
            'fields': ('payload', 'response_body')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(ConversionPixel)
class ConversionPixelAdmin(ModelAdmin):
    list_display = [
        'name', 'organization', 'campaign',
        'token', 'conversion_value', 'status', 'created_at'
    ]
    list_filter = ['status', 'campaign', 'organization']
    search_fields = ['name', 'token', 'campaign__name']
    ordering = ['-created_at']
    readonly_fields = ['id', 'token', 'created_at', 'updated_at']
    inlines = [ConversionEventInline]
    fieldsets = (
        ('Pixel Info', {
            'fields': (
                'id', 'organization', 'campaign',
                'name', 'token',
                'conversion_value', 'status'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(ConversionEvent)
class ConversionEventAdmin(ModelAdmin):
    list_display = [
        'pixel', 'call_log', 'caller_number',
        'conversion_value', 'source_ip', 'created_at'
    ]
    list_filter = ['pixel']
    search_fields = ['caller_number', 'source_ip', 'pixel__name']
    ordering = ['-created_at']
    readonly_fields = [
        'id', 'pixel', 'call_log', 'caller_number',
        'conversion_value', 'raw_payload',
        'source_ip', 'created_at'
    ]
    fieldsets = (
        ('Event Info', {
            'fields': (
                'id', 'pixel', 'call_log',
                'caller_number', 'conversion_value',
                'source_ip'
            )
        }),
        ('Raw Payload', {
            'fields': ('raw_payload',)
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )