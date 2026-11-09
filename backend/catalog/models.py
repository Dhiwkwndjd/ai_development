from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Book(models.Model):
    """Книга как библиографическая запись (без привязки к физическому экземпляру)."""

    title = models.CharField("Название", max_length=255)
    author = models.CharField("Автор(ы)", max_length=255)
    # ISBN необязателен, но если указан — уникален. В MySQL UNIQUE допускает
    # несколько NULL, поэтому для «нет ISBN» используется именно NULL, а не "".
    isbn = models.CharField("ISBN", max_length=17, unique=True, null=True, blank=True)
    publisher = models.CharField("Издательство", max_length=255, blank=True, default="")
    publication_year = models.PositiveSmallIntegerField(
        "Год издания",
        null=True,
        blank=True,
        validators=[MinValueValidator(1000), MaxValueValidator(9999)],
    )
    language = models.CharField("Язык", max_length=32, blank=True, default="")
    description = models.TextField("Описание", blank=True, default="")
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    class Meta:
        db_table = "books"
        verbose_name = "Книга"
        verbose_name_plural = "Книги"
        ordering = ["title", "id"]
        indexes = [
            models.Index(fields=["title"], name="books_title_idx"),
            models.Index(fields=["author", "title"], name="books_author_title_idx"),
        ]

    def __str__(self):
        return f"{self.title} — {self.author}"


class CopyStatus(models.TextChoices):
    AVAILABLE = "available", "В наличии"
    RESERVED = "reserved", "Зарезервирован"
    ISSUED = "issued", "Выдан"
    MAINTENANCE = "maintenance", "На обслуживании"
    LOST = "lost", "Утерян"
    WRITTEN_OFF = "written_off", "Списан"


class BookCopy(models.Model):
    """Физический экземпляр книги."""

    Status = CopyStatus

    # Книгу с экземплярами удалить нельзя (PROTECT) — история не должна теряться.
    book = models.ForeignKey(
        Book,
        on_delete=models.PROTECT,
        related_name="copies",
        verbose_name="Книга",
    )
    inventory_number = models.CharField("Инвентарный номер", max_length=64, unique=True)
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.AVAILABLE,
    )
    shelf_location = models.CharField("Расположение", max_length=128, blank=True, default="")
    notes = models.TextField("Примечания", blank=True, default="")
    acquired_at = models.DateField("Дата поступления", null=True, blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        db_table = "book_copies"
        verbose_name = "Экземпляр книги"
        verbose_name_plural = "Экземпляры книг"
        ordering = ["book_id", "inventory_number"]
        indexes = [
            # «сколько свободных экземпляров у книги»
            models.Index(fields=["book", "status"], name="copies_book_status_idx"),
            models.Index(fields=["status"], name="copies_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=[s.value for s in CopyStatus]),
                name="copies_status_valid",
            ),
        ]

    def __str__(self):
        return f"[{self.inventory_number}] {self.book.title}"
