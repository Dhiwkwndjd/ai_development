from django.conf import settings
from django.db import models


class ActionLog(models.Model):
    """
    Журнал действий (аудит). Записи — append-only.

    - `user` — NULL для анонимных/системных действий; при удалении пользователя
      запись сохраняется (SET_NULL).
    - `entity_type` + `entity_id` — «мягкая» ссылка на объект (без FK), чтобы запись
      жила независимо от удаления/изменения схемы целевой таблицы.
    - `action` — строковый код действия, например `booking.created`, `auth.login`.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_logs",
        verbose_name="Пользователь",
    )
    action = models.CharField("Действие", max_length=64)
    entity_type = models.CharField("Тип объекта", max_length=64, blank=True, default="")
    entity_id = models.BigIntegerField("ID объекта", null=True, blank=True)
    details = models.JSONField("Детали", default=dict, blank=True)
    ip_address = models.GenericIPAddressField("IP-адрес", null=True, blank=True)
    user_agent = models.CharField("User-Agent", max_length=255, blank=True, default="")
    created_at = models.DateTimeField("Время", auto_now_add=True)

    class Meta:
        db_table = "action_logs"
        verbose_name = "Запись журнала"
        verbose_name_plural = "Журнал действий"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["created_at"], name="logs_created_idx"),
            models.Index(fields=["user", "created_at"], name="logs_user_created_idx"),
            models.Index(fields=["action", "created_at"], name="logs_action_created_idx"),
            models.Index(fields=["entity_type", "entity_id"], name="logs_entity_idx"),
        ]

    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M:%S}] {self.action}"
