from django.contrib import admin

from .models import Book, BookCopy


class BookCopyInline(admin.TabularInline):
    model = BookCopy
    extra = 0


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "author", "isbn", "publication_year")
    search_fields = ("title", "author", "isbn")
    inlines = [BookCopyInline]


@admin.register(BookCopy)
class BookCopyAdmin(admin.ModelAdmin):
    list_display = ("id", "inventory_number", "book", "status", "shelf_location")
    list_filter = ("status",)
    search_fields = ("inventory_number", "book__title")
    list_select_related = ("book",)
