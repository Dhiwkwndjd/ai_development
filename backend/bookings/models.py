from django.conf import settings
from django.db import models
from django.db.models import Case, Q, Value, When


# ---------------------------------------------------------------------------
# Статусы
# ---------------------------------------------------------------------------
class BookingStatus(models.TextChoices):
    CREATED = "created", "Создано"
    CONFIRMED = "confirmed", "Подтверждено"
    COMPLETED = "completed", "Завершено"
    CANCELLED = "cancelled", "Отменено"


class QueueStatus(models.TextChoices):
    WAITING = "waiting", "Ожидает"
    NOTIFIED = "notified", "Уведомлён (книга освободилась)"
    FULFILLED = "fulfilled", "Выполнено (получил книгу)"
    CANCELLED = "cancelled", "Отменено"
    EXPIRED = "expired", "Истекло"


# «Активные» статусы: запись занимает место (экземпляр / позицию в очереди).
BOOKING_ACTIVE_STATUSES = (BookingStatus.CREATED, BookingStatus.CONFIRMED)
QUEUE_ACTIVE_STATUSES = (QueueStatus.WAITING, QueueStatus.NOTIFIED)


def _active_marker(active_statuses):
    """
    Сгенерированная (STORED GENERATED) колонка: 1 для активной записи, NULL для остальных.

    Зачем: MySQL не поддерживает частичные (условные) UNIQUE-индексы, а Django на MySQL
    игнорирует UniqueConstraint(condition=...). Но UNIQUE-индекс в MySQL допускает
    любое число NULL. Поэтому UNIQUE (..., active_marker) гарантирует уникальность
    только среди активных записей, а неактивных (история) может быть сколько угодно.
    """
    return models.GeneratedField(
        expression=Case(
            When(status__in=[s.value for s in active_statuses], then=Value(1)),
            default=Value(None),
            output_field=models.PositiveSmallIntegerField(null=True),
        ),
        output_field=models.PositiveSmallIntegerField(null=True),
        db_persist=True,
        editable=False,
        verbose_name="Маркер активности",
    )


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------
class BookingQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status__in=BOOKING_ACTIVE_STATUSES)


class Booking(models.Model):
    """Бронирование конкретного экземпляра книги читателем."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name="Читатель",
    )
    book_copy = models.ForeignKey(
        "catalog.BookCopy",
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name="Экземпляр",
    )
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=BookingStatus.choices,
        default=BookingStatus.CREATED,
    )
    # Кто обработал бронь (библиотекарь/админ). NULL — пока никто.
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_bookings",
        verbose_name="Обработал",
    )
    due_date = models.DateField("Срок возврата", null=True, blank=True)
    comment = models.TextField("Комментарий", blank=True, default="")

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    confirmed_at = models.DateTimeField("Подтверждено", null=True, blank=True)
    completed_at = models.DateTimeField("Завершено", null=True, blank=True)
    cancelled_at = models.DateTimeField("Отменено", null=True, blank=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    active_marker = _active_marker(BOOKING_ACTIVE_STATUSES)

    objects = BookingQuerySet.as_manager()

    class Meta:
        db_table = "bookings"
        verbose_name = "Бронирование"
        verbose_name_plural = "Бронирования"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "status"], name="bookings_user_status_idx"),
            models.Index(fields=["book_copy", "status"], name="bookings_copy_status_idx"),
            models.Index(fields=["status", "created_at"], name="bookings_status_created_idx"),
            models.Index(fields=["due_date"], name="bookings_due_date_idx"),
        ]
        constraints = [
            # Не более ОДНОЙ активной брони (created/confirmed) на один экземпляр.
            models.UniqueConstraint(
                fields=["book_copy", "active_marker"],
                name="uniq_active_booking_per_copy",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[s.value for s in BookingStatus]),
                name="bookings_status_valid",
            ),
        ]

    def __str__(self):
        return f"Booking #{self.pk} ({self.status})"

    @property
    def is_active(self) -> bool:
        return self.status in BOOKING_ACTIVE_STATUSES


# ---------------------------------------------------------------------------
# WaitingQueue
# ---------------------------------------------------------------------------
class WaitingQueueQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status__in=QUEUE_ACTIVE_STATUSES)

    def for_book(self, book):
        """Активная очередь на книгу в порядке постановки (первый — первый)."""
        return self.active().filter(book=book).order_by("id")


class WaitingQueue(models.Model):
    """
    Очередь читателей на книгу (на книгу в целом, а не на конкретный экземпляр).

    Порядок постановки
    ------------------
    Канонический порядок — по `id` (AUTO_INCREMENT выдаётся самой БД атомарно и
    строго возрастает), поэтому он не зависит от часов app-серверов и не имеет
    «ничьих». `queued_at` хранится для отображения/аналитики.

    Запрет повторной записи
    -----------------------
    UNIQUE (user, book, active_marker): пока у пользователя есть активная запись
    (waiting / notified) на книгу, вторую создать нельзя; после закрытия записи
    (fulfilled / cancelled / expired) можно встать в очередь снова.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="queue_entries",
        verbose_name="Читатель",
    )
    book = models.ForeignKey(
        "catalog.Book",
        on_delete=models.PROTECT,
        related_name="queue_entries",
        verbose_name="Книга",
    )
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=QueueStatus.choices,
        default=QueueStatus.WAITING,
    )
    queued_at = models.DateTimeField("Поставлен в очередь", auto_now_add=True)
    notified_at = models.DateTimeField("Уведомлён", null=True, blank=True)
    closed_at = models.DateTimeField("Закрыта", null=True, blank=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    active_marker = _active_marker(QUEUE_ACTIVE_STATUSES)

    objects = WaitingQueueQuerySet.as_manager()

    class Meta:
        db_table = "waiting_queue"
        verbose_name = "Запись очереди"
        verbose_name_plural = "Очередь читателей"
        ordering = ["id"]
        indexes = [
            # выборка активной очереди книги в порядке id
            models.Index(fields=["book", "active_marker"], name="queue_book_active_idx"),
            models.Index(fields=["user", "status"], name="queue_user_status_idx"),
            models.Index(fields=["status", "queued_at"], name="queue_status_queued_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "book", "active_marker"],
                name="uniq_active_queue_user_book",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[s.value for s in QueueStatus]),
                name="queue_status_valid",
            ),
        ]

    def __str__(self):
        return f"Queue #{self.pk}: user={self.user_id} book={self.book_id} ({self.status})"

    @property
    def is_active(self) -> bool:
        return self.status in QUEUE_ACTIVE_STATUSES

    @property
    def position(self):
        """Позиция в активной очереди (1 — первый). None, если запись неактивна."""
        if not self.is_active or self.pk is None:
            return None
        ahead = WaitingQueue.objects.active().filter(book_id=self.book_id, id__lt=self.pk).count()
        return ahead + 1
