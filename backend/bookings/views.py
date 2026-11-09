from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from common.permissions import IsStaffRole, is_staff_role
from users.models import User

from . import services
from .models import QUEUE_ACTIVE_STATUSES, Booking, WaitingQueue
from .serializers import (
    BookingCancelSerializer,
    BookingConfirmSerializer,
    BookingCreateSerializer,
    BookingSerializer,
    QueueEntrySerializer,
    QueueJoinSerializer,
    annotate_queue_positions,
)


class BookingViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, GenericViewSet):
    """
    Читатель видит и создаёт только свои брони. librarian/admin видят все и управляют статусами.
    Фильтры: ?status=, ?book=, ?active=true, ?user= (только staff).
    """

    serializer_class = BookingSerializer
    filter_backends = [OrderingFilter]
    ordering_fields = ["id", "created_at", "due_date"]
    ordering = ["-created_at", "-id"]

    def get_permissions(self):
        if self.action in ("confirm", "complete"):
            return [IsStaffRole()]
        return super().get_permissions()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # генерация OpenAPI-схемы
            return Booking.objects.none()
        qs = Booking.objects.select_related("user", "book_copy", "book_copy__book", "processed_by")
        user = self.request.user
        p = self.request.query_params
        if not is_staff_role(user):
            qs = qs.filter(user=user)
        elif p.get("user", "").isdigit():
            qs = qs.filter(user_id=int(p["user"]))
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("book", "").isdigit():
            qs = qs.filter(book_copy__book_id=int(p["book"]))
        if p.get("active") == "true":
            qs = qs.active()
        return qs

    def _respond(self, booking, code=status.HTTP_200_OK):
        booking = self.get_queryset().model.objects.select_related(
            "user", "book_copy", "book_copy__book", "processed_by"
        ).get(pk=booking.pk)
        return Response(BookingSerializer(booking).data, status=code)

    @extend_schema(request=BookingCreateSerializer, responses={201: BookingSerializer})
    def create(self, request, *args, **kwargs):
        s = BookingCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        target = s.validated_data.get("user", request.user)
        if target.pk != request.user.pk and not is_staff_role(request.user):
            raise PermissionDenied("Бронировать от имени другого пользователя может только librarian/admin.")
        booking = services.create_booking(
            user=target, book=s.validated_data["book"], actor=request.user, request=request
        )
        return self._respond(booking, status.HTTP_201_CREATED)

    @extend_schema(request=BookingConfirmSerializer, responses={200: BookingSerializer})
    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        s = BookingConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        booking = services.confirm_booking(
            booking_id=self.get_object().pk, actor=request.user,
            due_date=s.validated_data.get("due_date"), request=request,
        )
        return self._respond(booking)

    @extend_schema(request=None, responses={200: BookingSerializer})
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        booking = services.complete_booking(booking_id=self.get_object().pk, actor=request.user, request=request)
        return self._respond(booking)

    @extend_schema(request=BookingCancelSerializer, responses={200: BookingSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        s = BookingCancelSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        booking = services.cancel_booking(
            booking_id=self.get_object().pk, actor=request.user,
            comment=s.validated_data.get("comment", ""), request=request,
        )
        return self._respond(booking)


class QueueViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                   mixins.DestroyModelMixin, GenericViewSet):
    """
    Очередь на книгу. Читатель видит свои записи, staff — все.
    Фильтры: ?book=, ?status=, ?active=true, ?user= (staff). Порядок — по id (порядок постановки).
    DELETE — покинуть очередь (запись получает статус cancelled).
    """

    serializer_class = QueueEntrySerializer
    filter_backends = [OrderingFilter]
    ordering_fields = ["id", "queued_at"]
    ordering = ["id"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WaitingQueue.objects.none()
        qs = WaitingQueue.objects.select_related("user", "book")
        user = self.request.user
        p = self.request.query_params
        if not is_staff_role(user):
            qs = qs.filter(user=user)
        elif p.get("user", "").isdigit():
            qs = qs.filter(user_id=int(p["user"]))
        if p.get("book", "").isdigit():
            qs = qs.filter(book_id=int(p["book"]))
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("active") == "true":
            qs = qs.filter(status__in=QUEUE_ACTIVE_STATUSES)
        return annotate_queue_positions(qs)

    @extend_schema(request=QueueJoinSerializer, responses={201: QueueEntrySerializer})
    def create(self, request, *args, **kwargs):
        s = QueueJoinSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        entry = services.join_queue(user=request.user, book=s.validated_data["book"], request=request)
        entry = self.get_queryset().get(pk=entry.pk)
        return Response(QueueEntrySerializer(entry).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={204: None})
    def destroy(self, request, *args, **kwargs):
        services.leave_queue(entry_id=self.get_object().pk, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)
