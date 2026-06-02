from django.contrib import admin

# Register your models here.
from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import Publisher, PublisherCap, PublisherCampaign


class PublisherCapInline(TabularInline):
    model = PublisherCap
    extra = 0
    readonly_fields = ['id', 'created_at', 'updated_at']


class PublisherCampaignInline(TabularInline):
    model = PublisherCampaign
    extra = 0
    readonly_fields = ['id', 'created_at']


@admin.register(Publisher)
class PublisherAdmin(ModelAdmin):
    list_display = [
        'name', 'unique_id', 'organization', 'status',
        'email', 'phone_number', 'payout_amount', 'created_at'
    ]
    list_filter = ['status', 'organization']
    search_fields = ['name', 'email', 'phone_number', 'unique_id', 'description']
    ordering = ['-created_at']
    readonly_fields = ['id', 'unique_id', 'created_at', 'updated_at']
    inlines = [PublisherCapInline, PublisherCampaignInline]
    fieldsets = (
        ('Basic Info', {
            'fields': ('id', 'organization', 'created_by', 'name', 'description', 'status', 'unique_id')
        }),
        ('Contact', {
            'fields': ('email', 'phone_number')
        }),
        ('Financial', {
            'fields': ('payout_amount',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(PublisherCap)
class PublisherCapAdmin(ModelAdmin):
    list_display = ['publisher', 'max_calls_daily', 'max_calls_monthly', 'max_calls_global', 'created_at']
    search_fields = ['publisher__name']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(PublisherCampaign)
class PublisherCampaignAdmin(ModelAdmin):
    list_display = ['publisher', 'campaign', 'payout_amount', 'is_active', 'created_at']
    list_filter = ['is_active', 'campaign']
    search_fields = ['publisher__name', 'campaign__name']
    readonly_fields = ['id', 'created_at']