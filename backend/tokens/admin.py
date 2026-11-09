from django.contrib import admin

from .models import RefreshToken


@admin.register(RefreshToken)
class RefreshTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "created_at", "expires_at", "revoked_at", "ip_address")
    list_filter = ("revoked_at",)
    search_fields = ("user__email", "ip_address")
    raw_id_fields = ("user", "replaced_by")
    # хэш токена в админке не показываем
    exclude = ("token_hash",)
    readonly_fields = ("created_at", "last_used_at")
