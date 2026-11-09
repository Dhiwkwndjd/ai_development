from django.core.cache import cache
from rest_framework.test import APIClient, APITestCase

from catalog.models import Book, BookCopy
from users.models import Role, User
from tokens.services import create_access_token


class ApiTestCase(APITestCase):
    """Базовый класс: пользователи трёх ролей и хелперы авторизации."""

    def setUp(self):
        cache.clear()  # сбрасываем троттлинг между тестами
        self.reader = self.make_user("reader@example.com", Role.READER)
        self.reader2 = self.make_user("reader2@example.com", Role.READER)
        self.librarian = self.make_user("lib@example.com", Role.LIBRARIAN)
        self.admin = self.make_user("admin@example.com", Role.ADMIN)

    @staticmethod
    def make_user(email, role):
        return User.objects.create_user(email, "Str0ng-pass-123", full_name=email.split("@")[0], role=role)

    @staticmethod
    def client_for(user):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {create_access_token(user)}")
        return c

    @staticmethod
    def make_book(title="Война и мир", copies=1, **kw):
        book = Book.objects.create(title=title, author=kw.pop("author", "Л. Толстой"), **kw)
        for i in range(copies):
            BookCopy.objects.create(book=book, inventory_number=f"{title[:3]}-{book.pk}-{i}")
        return book
