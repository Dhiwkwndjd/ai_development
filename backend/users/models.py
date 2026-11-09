from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    READER = "reader", "Читатель"
    LIBRARIAN = "librarian", "Библиотекарь"
    ADMIN = "admin", "Администратор"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email обязателен.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.READER)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields["role"] != Role.ADMIN:
            raise ValueError("Суперпользователь должен иметь роль admin.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Пользователь системы.

    Роль хранится в отдельном поле `role` (reader / librarian / admin).
    Флаги is_staff / is_superuser нужны только для Django Admin.
    Пароль (`password`) и `last_login` приходят из AbstractBaseUser;
    пароль хранится только в виде хэша.
    """

    # PK: BigAutoField (id) — наследуется из DEFAULT_AUTO_FIELD
    email = models.EmailField("Email", max_length=254, unique=True)
    full_name = models.CharField("ФИО", max_length=255)
    phone = models.CharField("Телефон", max_length=32, blank=True, default="")
    role = models.CharField("Роль", max_length=16, choices=Role.choices, default=Role.READER)
    is_active = models.BooleanField("Активен", default=True)
    is_staff = models.BooleanField("Доступ в админку", default=False)
    date_joined = models.DateTimeField("Дата регистрации", default=timezone.now)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        db_table = "users"
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["role", "is_active"], name="users_role_active_idx"),
            models.Index(fields=["full_name"], name="users_full_name_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=[r.value for r in Role]),
                name="users_role_valid",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} <{self.email}>"

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.lower()
        super().save(*args, **kwargs)

    @property
    def is_reader(self) -> bool:
        return self.role == Role.READER

    @property
    def is_librarian(self) -> bool:
        return self.role == Role.LIBRARIAN

    @property
    def is_library_admin(self) -> bool:
        return self.role == Role.ADMIN
