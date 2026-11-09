from django.db.models import Count, ProtectedError, Q
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.viewsets import ModelViewSet

from bookings.models import QUEUE_ACTIVE_STATUSES
from bookings.services import notify_next_in_queue
from common.audit import log_action
from common.exceptions import Conflict
from common.permissions import IsStaffRole, ReadOnlyOrStaff

from .models import Book, BookCopy, CopyStatus
from .serializers import BookCopySerializer, BookSerializer


class BookViewSet(ModelViewSet):
    """Каталог: читать может любой авторизованный, менять — librarian/admin."""

    serializer_class = BookSerializer
    permission_classes = [ReadOnlyOrStaff]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["title", "author", "isbn"]
    ordering_fields = ["title", "author", "publication_year", "created_at"]
    ordering = ["title", "id"]

    def get_queryset(self):
        qs = Book.objects.annotate(
            available_copies=Count("copies", filter=Q(copies__status=CopyStatus.AVAILABLE), distinct=True),
            total_copies=Count(
                "copies",
                filter=~Q(copies__status__in=[CopyStatus.LOST, CopyStatus.WRITTEN_OFF]),
                distinct=True,
            ),
            queue_length=Count(
                "queue_entries",
                filter=Q(queue_entries__status__in=[s.value for s in QUEUE_ACTIVE_STATUSES]),
                distinct=True,
            ),
        )
        p = self.request.query_params
        if p.get("author"):
            qs = qs.filter(author__icontains=p["author"])
        if p.get("year", "").isdigit():
            qs = qs.filter(publication_year=int(p["year"]))
        if p.get("available") == "true":
            qs = qs.filter(available_copies__gt=0)
        return qs

    def perform_create(self, serializer):
        book = serializer.save()
        log_action(self.request, "book.created", entity=book)

    def perform_update(self, serializer):
        book = serializer.save()
        log_action(self.request, "book.updated", entity=book, details={"fields": list(serializer.validated_data)})

    def perform_destroy(self, instance):
        pk = instance.pk
        try:
            instance.delete()
        except ProtectedError:
            raise Conflict("Нельзя удалить книгу: у неё есть экземпляры или записи очереди.")
        log_action(self.request, "book.deleted", entity_type="book", entity_id=pk)


class BookCopyViewSet(ModelViewSet):
    """Экземпляры книг — только librarian/admin."""

    serializer_class = BookCopySerializer
    permission_classes = [IsStaffRole]
    queryset = BookCopy.objects.select_related("book")
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["inventory_number", "book__title", "shelf_location"]
    ordering_fields = ["id", "inventory_number", "status", "created_at"]
    ordering = ["book_id", "inventory_number"]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("book", "").isdigit():
            qs = qs.filter(book_id=int(p["book"]))
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        return qs

    def perform_create(self, serializer):
        copy = serializer.save()
        log_action(self.request, "copy.created", entity=copy, details={"book_id": copy.book_id})
        if copy.status == CopyStatus.AVAILABLE:
            notify_next_in_queue(copy.book_id, self.request)

    def perform_update(self, serializer):
        old_status = serializer.instance.status
        copy = serializer.save()
        log_action(self.request, "copy.updated", entity=copy, details={"fields": list(serializer.validated_data)})
        if copy.status == CopyStatus.AVAILABLE and old_status != CopyStatus.AVAILABLE:
            notify_next_in_queue(copy.book_id, self.request)

    def perform_destroy(self, instance):
        pk = instance.pk
        try:
            instance.delete()
        except ProtectedError:
            raise Conflict("Нельзя удалить экземпляр: есть связанные бронирования.")
        log_action(self.request, "copy.deleted", entity_type="bookcopy", entity_id=pk)
