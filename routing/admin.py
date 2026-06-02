from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import RoutingRule, RuleCondition, RuleDestination, CallLog


class RuleConditionInline(TabularInline):
    model = RuleCondition
    extra = 0
    readonly_fields = ['id', 'created_at']


class RuleDestinationInline(TabularInline):
    model = RuleDestination
    extra = 0
    readonly_fields = ['id', 'created_at']


@admin.register(RoutingRule)
class RoutingRuleAdmin(ModelAdmin):
    list_display = [
        'name', 'organization', 'campaign', 'rule_type',
        'priority', 'status', 'created_at'
    ]
    list_filter = ['status', 'rule_type', 'organization', 'campaign']
    search_fields = ['name', 'campaign__name']
    ordering = ['priority']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [RuleConditionInline, RuleDestinationInline]
    fieldsets = (
        ('Basic Info', {
            'fields': ('id', 'organization', 'campaign', 'created_by', 'name', 'rule_type', 'priority', 'status')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(RuleCondition)
class RuleConditionAdmin(ModelAdmin):
    list_display = ['rule', 'created_at']
    search_fields = ['rule__name']
    readonly_fields = ['id', 'created_at']


@admin.register(RuleDestination)
class RuleDestinationAdmin(ModelAdmin):
    list_display = [
        'destination', 'destination_type', 'rule',
        'buyer', 'priority', 'weight', 'created_at'
    ]
    list_filter = ['destination_type']
    search_fields = ['destination', 'rule__name', 'buyer__name']
    ordering = ['priority']
    readonly_fields = ['id', 'created_at']


@admin.register(CallLog)
class CallLogAdmin(ModelAdmin):
    list_display = [
        'caller_number', 'called_number', 'destination_number',
        'status', 'campaign', 'buyer', 'publisher',
        'duration', 'revenue', 'buyer_payout', 'publisher_payout',
        'ipqs_checked', 'ipqs_fraud_score', 'ipqs_block_reason',
        'created_at'
    ]
    list_filter = [
        'status', 'organization', 'campaign', 'buyer',
        'publisher', 'ipqs_checked', 'ipqs_is_voip'
    ]
    search_fields = [
        'caller_number', 'called_number', 'destination_number',
        'twilio_call_sid', 'ipqs_block_reason', 'caller_state',
        'caller_country'
    ]
    ordering = ['-created_at']
    readonly_fields = [
        'id', 'twilio_call_sid', 'created_at', 'updated_at',
        'answered_at', 'ended_at', 'ipqs_checked', 'ipqs_fraud_score',
        'ipqs_is_voip', 'ipqs_line_type', 'ipqs_block_reason',
        'twilio_cost', 'transcription', 'transcription_text',
        'transcription_status', 'sentiment', 'sentiment_score',
        'recording_url', 'recording_sid'
    ]
    fieldsets = (
        ('Call Info', {
            'fields': (
                'id', 'organization', 'campaign', 'routing_rule',
                'buyer', 'publisher', 'twilio_call_sid'
            )
        }),
        ('Numbers', {
            'fields': ('caller_number', 'called_number', 'destination_number')
        }),
        ('Status & Duration', {
            'fields': ('status', 'duration', 'answered_at', 'ended_at')
        }),
        ('Caller Location', {
            'fields': ('caller_area_code', 'caller_state', 'caller_country')
        }),
        ('Financials', {
            'fields': ('twilio_cost', 'buyer_payout', 'publisher_payout', 'revenue')
        }),
        ('IPQS Fraud Data', {
            'fields': (
                'ipqs_checked', 'ipqs_fraud_score', 'ipqs_is_voip',
                'ipqs_line_type', 'ipqs_block_reason'
            )
        }),
        ('Recording & Transcription', {
            'fields': (
                'recording_url', 'recording_sid', 'transcription',
                'transcription_text', 'transcription_status',
                'sentiment', 'sentiment_score'
            )
        }),
        ('Tags & Notes', {
            'fields': ('tags', 'notes')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )