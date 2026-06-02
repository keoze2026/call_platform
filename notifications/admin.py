from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import NotificationRule, NotificationLog


class NotificationLogInline(TabularInline):
    model = NotificationLog
    extra = 0
    readonly_fields = [
        'id', 'event', 'channel', 'recipient',
        'subject', 'body', 'status', 'error', 'created_at'
    ]


@admin.register(NotificationRule)
class NotificationRuleAdmin(ModelAdmin):
    list_display = [
        'name', 'organization', 'event',
        'channel', 'is_active', 'created_at'
    ]
    list_filter = ['event', 'channel', 'is_active', 'organization']
    search_fields = ['name', 'event']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [NotificationLogInline]
    fieldsets = (
        ('Rule Info', {
            'fields': (
                'id', 'organization', 'created_by',
                'name', 'event', 'channel',
                'recipients', 'is_active'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(NotificationLog)
class NotificationLogAdmin(ModelAdmin):
    list_display = [
        'event', 'organization', 'channel',
        'recipient', 'subject', 'status', 'created_at'
    ]
    list_filter = ['status', 'channel', 'event', 'organization']
    search_fields = ['recipient', 'subject', 'event', 'body', 'error']
    ordering = ['-created_at']
    readonly_fields = [
        'id', 'organization', 'rule', 'event',
        'channel', 'recipient', 'subject',
        'body', 'status', 'error', 'created_at'
    ]
    fieldsets = (
        ('Log Info', {
            'fields': (
                'id', 'organization', 'rule',
                'event', 'channel', 'recipient',
                'subject', 'status', 'error'
            )
        }),
        ('Message', {
            'fields': ('body',)
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )