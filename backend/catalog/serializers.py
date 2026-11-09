import re

from rest_framework import serializers

from bookings.models import Booking, BOOKING_ACTIVE_STATUSES

from .models import Book, BookCopy, CopyStatus

# Статусы, которые сотрудник может выставлять вручную. reserved/issued ставятся только бронированиями.
MANUAL_COPY_STATUSES = (
    CopyStatus.AVAILABLE,
    CopyStatus.MAINTENANCE,
    CopyStatus.LOST,
    CopyStatus.WRITTEN_OFF,
)


class BookSerializer(serializers.ModelSerializer):
    # аннотации queryset (в ответе на create/update отсутствуют)
    available_copies = serializers.IntegerField(read_only=True)
    total_copies = serializers.IntegerField(read_only=True)
    queue_length = serializers.IntegerField(read_only=True)

    class Meta:
        model = Book
        fields = [
            "id", "title", "author", "isbn", "publisher", "publication_year", "language",
            "description", "available_copies", "total_copies", "queue_length",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_isbn(self, value):
        if value in (None, ""):
            return None  # UNIQUE допускает много NULL, но не много ""
        normalized = re.sub(r"[\s-]", "", value).upper()
        if not re.fullmatch(r"\d{9}[\dX]|\d{13}", normalized):
            raise serializers.ValidationError("ISBN должен содержать 10 или 13 цифр.")
        qs = Book.objects.filter(isbn=normalized)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Книга с таким ISBN уже существует.")
        return normalized


class BookBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "author"]
        read_only_fields = fields


class BookCopySerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source="book.title", read_only=True)

    class Meta:
        model = BookCopy
        fields = [
            "id", "book", "book_title", "inventory_number", "status", "shelf_location",
            "notes", "acquired_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_status(self, value):
        if self.instance is None:
            if value not in MANUAL_COPY_STATUSES:
                raise serializers.ValidationError(
                    "Статус reserved/issued выставляется только через бронирования."
                )
            return value
        if value == self.instance.status:
            return value
        if Booking.objects.filter(book_copy=self.instance, status__in=BOOKING_ACTIVE_STATUSES).exists():
            raise serializers.ValidationError("Нельзя менять статус экземпляра с активным бронированием.")
        if value not in MANUAL_COPY_STATUSES or self.instance.status in (CopyStatus.RESERVED, CopyStatus.ISSUED):
            raise serializers.ValidationError("Недопустимое изменение статуса.")
        return value

    def validate_book(self, value):
        if self.instance is not None and value.pk != self.instance.book_id:
            raise serializers.ValidationError("Нельзя переносить экземпляр на другую книгу.")
        return value


class CopyBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookCopy
        fields = ["id", "inventory_number", "shelf_location"]
        read_only_fields = fields
