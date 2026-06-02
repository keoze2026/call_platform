from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Campaign


@admin.register(Campaign)
class CampaignAdmin(ModelAdmin):
    list_display = [
        'name', 'organization', 'status', 'routing_type',
        'payout_amount', 'revenue_amount', 'min_call_duration',
        'duplicate_call_block', 'created_at'
    ]
    list_filter = ['status', 'routing_type', 'organization', 'greeting_enabled', 'whisper_enabled', 'auto_sms_enabled']
    search_fields = ['name', 'description']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fieldsets = (
        ('Basic Info', {
            'fields': ('id', 'organization', 'created_by', 'name', 'description', 'status', 'routing_type')
        }),
        ('Financial', {
            'fields': ('payout_amount', 'revenue_amount')
        }),
        ('Call Settings', {
            'fields': ('min_call_duration', 'duplicate_call_block', 'duplicate_call_block_hours')
        }),
        ('Greeting & Whisper', {
            'fields': ('greeting_enabled', 'greeting_message', 'whisper_enabled', 'whisper_message')
        }),
        ('Auto SMS', {
            'fields': ('auto_sms_enabled', 'auto_sms_message')
        }),
        ('IPQS Spam Protection', {
            'fields': (
                'ipqs_enabled', 'block_voip', 'block_risky',
                'block_spammer', 'block_recent_abuse',
                'block_invalid_numbers', 'max_fraud_score'
            )
        }),
        ('Queue Settings', {
            'fields': ('queue_enabled', 'queue_max_size', 'queue_max_wait_seconds')
        }),
        ('RTB Settings', {
            'fields': ('bid_floor', 'rtb_timeout_seconds')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )