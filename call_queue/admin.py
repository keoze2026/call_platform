from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import CallQueue


@admin.register(CallQueue)
class CallQueueAdmin(ModelAdmin):
    list_display = [
        'caller_number', 'called_number', 'organization',
        'campaign', 'status', 'position',
        'wait_seconds', 'enqueued_at',
        'connected_at', 'ended_at'
    ]
    list_filter = ['status', 'organization', 'campaign']
    search_fields = [
        'caller_number', 'called_number',
        'twilio_call_sid', 'twilio_queue_sid',
        'campaign__name'
    ]
    ordering = ['enqueued_at']
    readonly_fields = [
        'id', 'twilio_call_sid', 'twilio_queue_sid',
        'enqueued_at', 'connected_at', 'ended_at',
        'created_at', 'updated_at'
    ]
    fieldsets = (
        ('Call Info', {
            'fields': (
                'id', 'organization', 'campaign',
                'caller_number', 'called_number',
                'twilio_call_sid', 'twilio_queue_sid'
            )
        }),
        ('Queue Status', {
            'fields': ('status', 'position', 'wait_seconds')
        }),
        ('Timestamps', {
            'fields': (
                'enqueued_at', 'connected_at',
                'ended_at', 'created_at', 'updated_at'
            )
        }),
    )