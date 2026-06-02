from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import Buyer, BuyerCap, BuyerCampaign, BuyerSchedule


class BuyerCapInline(TabularInline):
    model = BuyerCap
    extra = 0
    readonly_fields = ['id', 'created_at', 'updated_at']


class BuyerScheduleInline(TabularInline):
    model = BuyerSchedule
    extra = 0
    readonly_fields = ['id', 'created_at']


class BuyerCampaignInline(TabularInline):
    model = BuyerCampaign
    extra = 0
    readonly_fields = ['id', 'created_at']


@admin.register(Buyer)
class BuyerAdmin(ModelAdmin):
    list_display = [
        'name', 'organization', 'status', 'routing_type',
        'phone_number', 'payout_amount', 'quality_score',
        'max_concurrency', 'dup_window_days', 'created_at'
    ]
    list_filter = ['status', 'routing_type', 'organization']
    search_fields = ['name', 'phone_number', 'sip_endpoint', 'description']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at', 'quality_score_updated_at']
    inlines = [BuyerCapInline, BuyerCampaignInline, BuyerScheduleInline]
    fieldsets = (
        ('Basic Info', {
            'fields': ('id', 'organization', 'created_by', 'name', 'description', 'status')
        }),
        ('Routing', {
            'fields': ('routing_type', 'phone_number', 'sip_endpoint', 'rtb_endpoint')
        }),
        ('Financial', {
            'fields': ('payout_amount',)
        }),
        ('Call Settings', {
            'fields': ('min_call_duration', 'max_concurrency', 'dup_window_days')
        }),
        ('Quality Score', {
            'fields': ('quality_score', 'quality_score_updated_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(BuyerCap)
class BuyerCapAdmin(ModelAdmin):
    list_display = ['buyer', 'max_calls_daily', 'max_calls_monthly', 'max_calls_global', 'max_concurrency', 'created_at']
    search_fields = ['buyer__name']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(BuyerCampaign)
class BuyerCampaignAdmin(ModelAdmin):
    list_display = ['buyer', 'campaign', 'priority', 'weight', 'is_active', 'created_at']
    list_filter = ['is_active', 'campaign']
    search_fields = ['buyer__name', 'campaign__name']
    ordering = ['priority']
    readonly_fields = ['id', 'created_at']


@admin.register(BuyerSchedule)
class BuyerScheduleAdmin(ModelAdmin):
    list_display = ['buyer', 'day_of_week', 'start_time', 'end_time', 'timezone', 'is_active']
    list_filter = ['day_of_week', 'is_active', 'timezone']
    search_fields = ['buyer__name']
    readonly_fields = ['id', 'created_at']