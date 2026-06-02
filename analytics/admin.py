from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import CallRecord


@admin.register(CallRecord)
class CallRecordAdmin(ModelAdmin):
    list_display = [
        'caller_number', 'called_number', 'campaign_name',
        'buyer_name', 'publisher_name', 'status',
        'routing_type', 'duration_seconds', 'revenue',
        'payout', 'is_converted', 'is_duplicate', 'created_at'
    ]
    list_filter = [
        'status', 'routing_type', 'organization',
        'is_converted', 'is_duplicate', 'is_spam'
    ]
    search_fields = [
        'caller_number', 'called_number',
        'campaign_name', 'buyer_name',
        'publisher_name', 'caller_state',
        'twilio_call_sid'
    ]
    ordering = ['-created_at']
    readonly_fields = [
        'id', 'organization', 'twilio_call_sid',
        'caller_number', 'called_number', 'caller_state',
        'campaign_id', 'campaign_name',
        'buyer_id', 'buyer_name',
        'publisher_id', 'publisher_name',
        'status', 'routing_type',
        'duration_seconds', 'billable_seconds',
        'revenue', 'payout', 'profit',
        'winning_bid', 'auction_id',
        'is_converted', 'is_duplicate', 'is_spam',
        'recording_url', 'started_at',
        'answered_at', 'ended_at', 'created_at', 'updated_at'
    ]
    fieldsets = (
        ('Call Info', {
            'fields': (
                'id', 'organization', 'twilio_call_sid',
                'caller_number', 'called_number', 'caller_state'
            )
        }),
        ('Campaign & Parties', {
            'fields': (
                'campaign_id', 'campaign_name',
                'buyer_id', 'buyer_name',
                'publisher_id', 'publisher_name'
            )
        }),
        ('Outcome', {
            'fields': (
                'status', 'routing_type',
                'duration_seconds', 'billable_seconds',
                'is_converted', 'is_duplicate', 'is_spam',
                'started_at', 'answered_at', 'ended_at'
            )
        }),
        ('Financials', {
            'fields': ('revenue', 'payout', 'profit')
        }),
        ('RTB', {
            'fields': ('winning_bid', 'auction_id')
        }),
        ('Recording', {
            'fields': ('recording_url',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )