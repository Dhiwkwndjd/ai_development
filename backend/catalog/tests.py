from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from .models import Book, BookCopy


class CatalogTests(TestCase):
    def test_multiple_books_without_isbn_allowed(self):
        Book.objects.create(title="A", author="X")
        Book.objects.create(title="B", author="Y")

    def test_isbn_unique_when_set(self):
        Book.objects.create(title="A", author="X", isbn="9785170000001")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Book.objects.create(title="B", author="Y", isbn="9785170000001")

    def test_inventory_number_unique(self):
        b = Book.objects.create(title="A", author="X")
        BookCopy.objects.create(book=b, inventory_number="INV-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            BookCopy.objects.create(book=b, inventory_number="INV-1")

    def test_book_with_copies_cannot_be_deleted(self):
        b = Book.objects.create(title="A", author="X")
        BookCopy.objects.create(book=b, inventory_number="INV-1")
        with self.assertRaises(ProtectedError):
            b.delete()
