from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import PhoneNumber


@admin.register(PhoneNumber)
class PhoneNumberAdmin(ModelAdmin):
    list_display = [
        'number', 'friendly_name', 'organization', 'number_type',
        'status', 'country_code', 'campaign', 'publisher',
        'voice_enabled', 'sms_enabled', 'created_at'
    ]
    list_filter = [
        'status', 'number_type', 'country_code',
        'voice_enabled', 'sms_enabled', 'organization'
    ]
    search_fields = [
        'number', 'friendly_name',
        'twilio_sid', 'campaign__name',
        'publisher__name'
    ]
    ordering = ['-created_at']
    readonly_fields = ['id', 'twilio_sid', 'created_at', 'updated_at']
    fieldsets = (
        ('Number Info', {
            'fields': (
                'id', 'organization', 'created_by',
                'number', 'friendly_name',
                'number_type', 'status', 'country_code'
            )
        }),
        ('Twilio', {
            'fields': ('twilio_sid',)
        }),
        ('Assignment', {
            'fields': ('campaign', 'publisher')
        }),
        ('Capabilities', {
            'fields': ('voice_enabled', 'sms_enabled')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )