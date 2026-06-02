from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import RTBAuction, RTBBid


class RTBBidInline(TabularInline):
    model = RTBBid
    extra = 0
    readonly_fields = ['id', 'buyer', 'bid_amount', 'status', 'response_ms', 'raw_response', 'created_at', 'updated_at']


@admin.register(RTBAuction)
class RTBAuctionAdmin(ModelAdmin):
    list_display = [
        'caller_number', 'campaign', 'organization_id',
        'status', 'winning_bid', 'winner',
        'duration_ms', 'created_at'
    ]
    list_filter = ['status', 'campaign']
    search_fields = [
        'caller_number', 'twilio_call_sid',
        'caller_state', 'campaign__name',
        'winner__name'
    ]
    ordering = ['-created_at']
    readonly_fields = [
        'id', 'created_at', 'updated_at',
        'winning_bid', 'winner', 'duration_ms'
    ]
    inlines = [RTBBidInline]
    fieldsets = (
        ('Auction Info', {
            'fields': (
                'id', 'organization_id', 'campaign',
                'caller_number', 'caller_state',
                'twilio_call_sid', 'status'
            )
        }),
        ('Result', {
            'fields': ('winning_bid', 'winner', 'duration_ms')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(RTBBid)
class RTBBidAdmin(ModelAdmin):
    list_display = [
        'buyer', 'auction', 'bid_amount',
        'status', 'response_ms', 'created_at'
    ]
    list_filter = ['status', 'buyer']
    search_fields = ['buyer__name', 'auction__caller_number']
    ordering = ['-created_at']
    readonly_fields = [
        'id', 'auction', 'buyer', 'bid_amount',
        'status', 'response_ms', 'raw_response',
        'created_at', 'updated_at'
    ]
    fieldsets = (
        ('Bid Info', {
            'fields': (
                'id', 'auction', 'buyer',
                'bid_amount', 'status', 'response_ms'
            )
        }),
        ('Raw Response', {
            'fields': ('raw_response',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )