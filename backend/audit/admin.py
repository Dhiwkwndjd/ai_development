from django.contrib import admin

from .models import ActionLog


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "user", "action", "entity_type", "entity_id")
    list_filter = ("action", "entity_type")
    search_fields = ("user__email", "action")
    raw_id_fields = ("user",)

    # журнал только для чтения
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
