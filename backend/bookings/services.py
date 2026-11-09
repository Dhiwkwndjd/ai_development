"""
Бизнес-логика бронирований и очереди.

Жизненный цикл брони и экземпляра:

    POST /bookings/            Booking.created    BookCopy.reserved   (читатель забронировал)
    POST /bookings/{id}/confirm   -> confirmed    BookCopy.issued     (библиотекарь выдал книгу)
    POST /bookings/{id}/complete  -> completed    BookCopy.available  (книга возвращена)
    POST /bookings/{id}/cancel    -> cancelled    BookCopy.available

Каждый раз, когда экземпляр освобождается, первым в очереди (по id) рассылается
уведомление: запись WAITING -> NOTIFIED. Число NOTIFIED никогда не превышает число свободных
экземпляров, поэтому «чужой» свободный экземпляр нельзя забрать в обход очереди.

Конкурентность: изменения идут в транзакции с SELECT ... FOR UPDATE, а UNIQUE-ограничения
БД (одна активная бронь на экземпляр, одна активная запись очереди на пару user+book)
служат последней линией защиты — IntegrityError превращается в 409.
"""
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from catalog.models import BookCopy, CopyStatus
from common.audit import log_action
from common.exceptions import Conflict
from common.permissions import is_staff_role
from users.models import User

from .models import (
    BOOKING_ACTIVE_STATUSES,
    QUEUE_ACTIVE_STATUSES,
    Booking,
    BookingStatus,
    QueueStatus,
    WaitingQueue,
)


def _cfg(name):
    return settings.LIBRARY[name]


# ---------------------------------------------------------------------------
# Очередь: уведомление следующего
# ---------------------------------------------------------------------------
def notify_next_in_queue(book_id, request=None):
    """Переводит в NOTIFIED столько первых WAITING, сколько есть «незанятых» свободных экземпляров."""
    with transaction.atomic():
        available = BookCopy.objects.filter(book_id=book_id, status=CopyStatus.AVAILABLE).count()
        notified = WaitingQueue.objects.filter(book_id=book_id, status=QueueStatus.NOTIFIED).count()
        slots = available - notified
        if slots <= 0:
            return []
        entries = list(
            WaitingQueue.objects.select_for_update()
            .filter(book_id=book_id, status=QueueStatus.WAITING)
            .order_by("id")[:slots]
        )
        now = timezone.now()
        for entry in entries:
            entry.status = QueueStatus.NOTIFIED
            entry.notified_at = now
            entry.save(update_fields=["status", "notified_at", "updated_at"])
            log_action(
                request, "queue.notified", entity=entry,
                details={"book_id": book_id, "user_id": entry.user_id},
            )
        return entries


def _free_slots(book_id):
    available = BookCopy.objects.filter(book_id=book_id, status=CopyStatus.AVAILABLE).count()
    notified = WaitingQueue.objects.filter(book_id=book_id, status=QueueStatus.NOTIFIED).count()
    return available - notified


# ---------------------------------------------------------------------------
# Бронирования
# ---------------------------------------------------------------------------
def create_booking(*, user, book, actor, request=None):
    if not user.is_active:
        raise ValidationError({"user": "Пользователь заблокирован."})
    try:
        with transaction.atomic():
            return _create_booking(user, book, actor, request)
    except IntegrityError as exc:
        raise Conflict("Экземпляр уже занят. Повторите попытку.") from exc


def _create_booking(user, book, actor, request):
    # Блокировка строки пользователя сериализует его параллельные запросы (лимит, дубли).
    User.objects.select_for_update().get(pk=user.pk)

    active = Booking.objects.active().filter(user=user)
    if active.count() >= _cfg("MAX_ACTIVE_BOOKINGS_PER_USER"):
        raise Conflict(f"Достигнут лимит активных бронирований ({_cfg('MAX_ACTIVE_BOOKINGS_PER_USER')}).")
    if active.filter(book_copy__book=book).exists():
        raise Conflict("У пользователя уже есть активное бронирование этой книги.")

    copies = list(
        BookCopy.objects.select_for_update(skip_locked=True)
        .filter(book=book, status=CopyStatus.AVAILABLE)
        .order_by("id")
    )
    entry = (
        WaitingQueue.objects.select_for_update()
        .filter(book=book, user=user, status__in=QUEUE_ACTIVE_STATUSES)
        .first()
    )
    notified_total = WaitingQueue.objects.filter(book=book, status=QueueStatus.NOTIFIED).count()

    if entry is not None and entry.status == QueueStatus.NOTIFIED:
        allowed = len(copies) >= 1  # свой «слот» уже закреплён за ним
    else:
        allowed = len(copies) - notified_total > 0
    if not allowed:
        raise Conflict("Нет свободных экземпляров. Встаньте в очередь: POST /api/v1/queue/.")

    copy = copies[0]
    copy.status = CopyStatus.RESERVED
    copy.save(update_fields=["status", "updated_at"])
    booking = Booking.objects.create(user=user, book_copy=copy)

    if entry is not None:
        entry.status = QueueStatus.FULFILLED
        entry.closed_at = timezone.now()
        entry.save(update_fields=["status", "closed_at", "updated_at"])
        log_action(request, "queue.fulfilled", user=actor, entity=entry)

    log_action(
        request, "booking.created", user=actor, entity=booking,
        details={"book_id": book.pk, "copy_id": copy.pk, "for_user_id": user.pk},
    )
    return booking


def _locked_booking(booking_id):
    booking = Booking.objects.select_for_update().filter(pk=booking_id).first()
    if booking is None:
        raise ValidationError("Бронирование не найдено.")
    copy = BookCopy.objects.select_for_update().get(pk=booking.book_copy_id)
    return booking, copy


def confirm_booking(*, booking_id, actor, due_date=None, request=None):
    """created -> confirmed: библиотекарь выдаёт книгу."""
    with transaction.atomic():
        booking, copy = _locked_booking(booking_id)
        if booking.status != BookingStatus.CREATED:
            raise Conflict(f"Подтвердить можно только бронь в статусе created (сейчас {booking.status}).")
        today = timezone.localdate()
        if due_date is None:
            due_date = today + timedelta(days=_cfg("LOAN_DAYS"))
        elif due_date < today:
            raise ValidationError({"due_date": "Срок возврата не может быть в прошлом."})
        now = timezone.now()
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = now
        booking.processed_by = actor
        booking.due_date = due_date
        booking.save(update_fields=["status", "confirmed_at", "processed_by", "due_date", "updated_at"])
        copy.status = CopyStatus.ISSUED
        copy.save(update_fields=["status", "updated_at"])
        log_action(request, "booking.confirmed", user=actor, entity=booking, details={"due_date": str(due_date)})
        return booking


def complete_booking(*, booking_id, actor, request=None):
    """confirmed -> completed: книга возвращена."""
    with transaction.atomic():
        booking, copy = _locked_booking(booking_id)
        if booking.status != BookingStatus.CONFIRMED:
            raise Conflict(f"Завершить можно только подтверждённую бронь (сейчас {booking.status}).")
        booking.status = BookingStatus.COMPLETED
        booking.completed_at = timezone.now()
        booking.processed_by = actor
        booking.save(update_fields=["status", "completed_at", "processed_by", "updated_at"])
        copy.status = CopyStatus.AVAILABLE
        copy.save(update_fields=["status", "updated_at"])
        log_action(request, "booking.completed", user=actor, entity=booking)
        notify_next_in_queue(copy.book_id, request)
        return booking


def cancel_booking(*, booking_id, actor, comment="", request=None):
    """created/confirmed -> cancelled. Читатель — только свою и только в статусе created."""
    with transaction.atomic():
        booking, copy = _locked_booking(booking_id)
        staff = is_staff_role(actor)
        if not staff and booking.user_id != actor.pk:
            raise PermissionDenied("Нельзя отменить чужое бронирование.")
        if booking.status not in BOOKING_ACTIVE_STATUSES:
            raise Conflict(f"Бронь уже в статусе {booking.status}.")
        if not staff and booking.status != BookingStatus.CREATED:
            raise Conflict("Книга уже выдана — отмену оформляет библиотекарь.")
        _do_cancel(booking, copy, actor, comment, request, action="booking.cancelled")
        return booking


def _do_cancel(booking, copy, actor, comment, request, action):
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = timezone.now()
    if actor is not None:
        booking.processed_by = actor if is_staff_role(actor) else booking.processed_by
    if comment:
        booking.comment = comment
    booking.save(update_fields=["status", "cancelled_at", "processed_by", "comment", "updated_at"])
    copy.status = CopyStatus.AVAILABLE
    copy.save(update_fields=["status", "updated_at"])
    log_action(request, action, user=actor, entity=booking)
    notify_next_in_queue(copy.book_id, request)


# ---------------------------------------------------------------------------
# Очередь
# ---------------------------------------------------------------------------
def join_queue(*, user, book, request=None):
    try:
        with transaction.atomic():
            User.objects.select_for_update().get(pk=user.pk)
            if WaitingQueue.objects.filter(user=user, book=book, status__in=QUEUE_ACTIVE_STATUSES).exists():
                raise Conflict("Вы уже стоите в очереди на эту книгу.")
            if Booking.objects.active().filter(user=user, book_copy__book=book).exists():
                raise Conflict("У вас уже есть активное бронирование этой книги.")
            in_circulation = BookCopy.objects.filter(book=book).exclude(
                status__in=[CopyStatus.LOST, CopyStatus.WRITTEN_OFF]
            )
            if not in_circulation.exists():
                raise Conflict("У книги нет экземпляров в обороте — очередь невозможна.")
            if _free_slots(book.pk) > 0:
                raise Conflict("Есть свободные экземпляры — создайте бронирование.")
            entry = WaitingQueue.objects.create(user=user, book=book)
            log_action(request, "queue.joined", user=user, entity=entry, details={"book_id": book.pk})
            return entry
    except IntegrityError as exc:
        raise Conflict("Вы уже стоите в очереди на эту книгу.") from exc


def leave_queue(*, entry_id, actor, request=None):
    with transaction.atomic():
        entry = WaitingQueue.objects.select_for_update().filter(pk=entry_id).first()
        if entry is None:
            raise ValidationError("Запись не найдена.")
        if not is_staff_role(actor) and entry.user_id != actor.pk:
            raise PermissionDenied("Нельзя покинуть чужую очередь.")
        if entry.status not in QUEUE_ACTIVE_STATUSES:
            raise Conflict(f"Запись уже в статусе {entry.status}.")
        was_notified = entry.status == QueueStatus.NOTIFIED
        entry.status = QueueStatus.CANCELLED
        entry.closed_at = timezone.now()
        entry.save(update_fields=["status", "closed_at", "updated_at"])
        log_action(request, "queue.left", user=actor, entity=entry)
        if was_notified:  # освободившийся «слот» переходит следующему
            notify_next_in_queue(entry.book_id, request)
        return entry


# ---------------------------------------------------------------------------
# Истечение сроков (вызывается командой process_expirations, например по cron)
# ---------------------------------------------------------------------------
def expire_stale_bookings():
    """Отменяет неподтверждённые брони старше BOOKING_HOLD_HOURS."""
    threshold = timezone.now() - timedelta(hours=_cfg("BOOKING_HOLD_HOURS"))
    ids = list(
        Booking.objects.filter(status=BookingStatus.CREATED, created_at__lt=threshold).values_list("id", flat=True)
    )
    done = 0
    for pk in ids:
        with transaction.atomic():
            booking, copy = _locked_booking(pk)
            if booking.status != BookingStatus.CREATED:
                continue
            _do_cancel(booking, copy, None, "Отменено автоматически: истёк срок подтверждения.", None,
                       action="booking.expired")
            done += 1
    return done


def expire_stale_notifications():
    """Закрывает уведомления старше QUEUE_NOTIFY_HOURS; слот передаётся следующему."""
    threshold = timezone.now() - timedelta(hours=_cfg("QUEUE_NOTIFY_HOURS"))
    rows = list(
        WaitingQueue.objects.filter(status=QueueStatus.NOTIFIED, notified_at__lt=threshold)
        .values_list("id", "book_id")
    )
    done = 0
    for pk, book_id in rows:
        with transaction.atomic():
            entry = WaitingQueue.objects.select_for_update().get(pk=pk)
            if entry.status != QueueStatus.NOTIFIED:
                continue
            entry.status = QueueStatus.EXPIRED
            entry.closed_at = timezone.now()
            entry.save(update_fields=["status", "closed_at", "updated_at"])
            log_action(None, "queue.expired", entity=entry, details={"user_id": entry.user_id})
            notify_next_in_queue(book_id)
            done += 1
    return done
