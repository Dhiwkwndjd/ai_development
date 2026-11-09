from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.catalog.models import Book
from apps.catalog.serializers import BookBriefSerializer, CopyBriefSerializer
from apps.users.models import User
from apps.users.serializers import PersonSerializer, UserBriefSerializer

from .models import QUEUE_ACTIVE_STATUSES, Booking, WaitingQueue


class BookingSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    book = BookBriefSerializer(source="book_copy.book", read_only=True)
    book_copy = CopyBriefSerializer(read_only=True)
    processed_by = PersonSerializer(read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id", "user", "book", "book_copy", "status", "due_date", "comment", "processed_by",
            "created_at", "confirmed_at", "completed_at", "cancelled_at", "updated_at",
        ]
        read_only_fields = fields


class BookingCreateSerializer(serializers.Serializer):
    book = serializers.PrimaryKeyRelatedField(queryset=Book.objects.all())
    # Только для librarian/admin: оформить бронь от имени читателя.
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)


class BookingConfirmSerializer(serializers.Serializer):
    due_date = serializers.DateField(required=False)


class BookingCancelSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class QueueEntrySerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    book = BookBriefSerializer(read_only=True)
    position = serializers.SerializerMethodField()

    class Meta:
        model = WaitingQueue
        fields = ["id", "user", "book", "status", "position", "queued_at", "notified_at", "closed_at"]
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_position(self, obj):
        if not obj.is_active:
            return None
        ahead = getattr(obj, "_ahead", None)
        return ahead + 1 if ahead is not None else obj.position


class QueueJoinSerializer(serializers.Serializer):
    book = serializers.PrimaryKeyRelatedField(queryset=Book.objects.all())


def annotate_queue_positions(qs):
    """Позиция в очереди одним запросом (без N+1)."""
    ahead = (
        WaitingQueue.objects.filter(
            book_id=OuterRef("book_id"),
            status__in=[s.value for s in QUEUE_ACTIVE_STATUSES],
            id__lt=OuterRef("id"),
        )
        .order_by()
        .values("book_id")
        .annotate(c=Count("id"))
        .values("c")
    )
    return qs.annotate(_ahead=Coalesce(Subquery(ahead), 0))
