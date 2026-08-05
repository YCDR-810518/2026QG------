from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Node
from .models import Alert


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ('node_id', 'node_name', 'node_type', 'people', 'density', 'level')
    search_fields = ('node_id', 'node_name')
    list_filter = ('node_type', 'level')


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ('alert_id', 'node_name', 'risk_level', 'status', 'trigger_source', 'created_at')
    search_fields = ('alert_id', 'node_name')
    list_filter = ('risk_level', 'status', 'trigger_source')