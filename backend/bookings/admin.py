from django.contrib import admin
from .models import Booking, WaitingQueue


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "book_copy", "status", "due_date", "created_at")
    list_filter = ("status",)
    search_fields = ("user__email", "book_copy__inventory_number", "book_copy__book__title")
    list_select_related = ("user", "book_copy", "book_copy__book")
    raw_id_fields = ("user", "book_copy", "processed_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WaitingQueue)
class WaitingQueueAdmin(admin.ModelAdmin):
    list_display = ("id", "book", "user", "status", "queued_at")
    list_filter = ("status",)
    search_fields = ("user__email", "book__title")
    list_select_related = ("user", "book")
    raw_id_fields = ("user", "book")
    readonly_fields = ("queued_at", "updated_at")
