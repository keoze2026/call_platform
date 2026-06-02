from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import IVRFlow, IVRNode, IVRNodeTransition


class IVRNodeInline(TabularInline):
    model = IVRNode
    extra = 0
    readonly_fields = ['id', 'created_at', 'updated_at']


class IVRNodeTransitionInline(TabularInline):
    model = IVRNodeTransition
    extra = 0
    readonly_fields = ['id', 'created_at']
    fk_name = 'from_node'


@admin.register(IVRFlow)
class IVRFlowAdmin(ModelAdmin):
    list_display = [
        'name', 'organization', 'campaign',
        'status', 'language', 'voice', 'created_at'
    ]
    list_filter = ['status', 'language', 'voice', 'organization']
    search_fields = ['name', 'description', 'campaign__name']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [IVRNodeInline]
    fieldsets = (
        ('Flow Info', {
            'fields': (
                'id', 'organization', 'created_by',
                'campaign', 'name', 'description', 'status'
            )
        }),
        ('Voice Settings', {
            'fields': ('welcome_message', 'language', 'voice')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(IVRNode)
class IVRNodeAdmin(ModelAdmin):
    list_display = [
        'name', 'flow', 'node_type',
        'position', 'is_entry_point', 'created_at'
    ]
    list_filter = ['node_type', 'is_entry_point', 'flow']
    search_fields = ['name', 'flow__name']
    ordering = ['flow', 'position']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [IVRNodeTransitionInline]
    fieldsets = (
        ('Node Info', {
            'fields': (
                'id', 'flow', 'name', 'node_type',
                'position', 'is_entry_point'
            )
        }),
        ('Configuration', {
            'fields': ('config',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(IVRNodeTransition)
class IVRNodeTransitionAdmin(ModelAdmin):
    list_display = ['from_node', 'to_node', 'trigger', 'created_at']
    list_filter = ['trigger']
    search_fields = ['from_node__name', 'to_node__name', 'trigger']
    ordering = ['from_node']
    readonly_fields = ['id', 'created_at']
    fieldsets = (
        ('Transition Info', {
            'fields': ('id', 'from_node', 'to_node', 'trigger')
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )