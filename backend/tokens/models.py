from django.conf import settings
from django.db import models
from django.utils import timezone


class RefreshToken(models.Model):
    """
    Refresh-токен.

    В БД хранится ТОЛЬКО SHA-256 хэш токена (hex, 64 символа) — сам токен выдаётся
    клиенту один раз и восстановить его из БД нельзя.

    Ротация: при обновлении старый токен отзывается (`revoked_at`), а в `replaced_by`
    записывается новый. Повторное использование отозванного токена можно трактовать
    как компрометацию цепочки (логика будет в слое сервисов).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="refresh_tokens",
        verbose_name="Пользователь",
    )
    token_hash = models.CharField("SHA-256 хэш токена", max_length=64, unique=True)
    created_at = models.DateTimeField("Выдан", auto_now_add=True)
    expires_at = models.DateTimeField("Истекает")
    revoked_at = models.DateTimeField("Отозван", null=True, blank=True)
    last_used_at = models.DateTimeField("Последнее использование", null=True, blank=True)
    replaced_by = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replaces",
        verbose_name="Заменён токеном",
    )
    user_agent = models.CharField("User-Agent", max_length=255, blank=True, default="")
    ip_address = models.GenericIPAddressField("IP-адрес", null=True, blank=True)

    class Meta:
        db_table = "refresh_tokens"
        verbose_name = "Refresh-токен"
        verbose_name_plural = "Refresh-токены"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "revoked_at"], name="rtokens_user_revoked_idx"),
            models.Index(fields=["expires_at"], name="rtokens_expires_idx"),
        ]

    def __str__(self):
        return f"RefreshToken #{self.pk} user={self.user_id}"

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @property
    def is_valid(self) -> bool:
        return not self.is_revoked and not self.is_expired
