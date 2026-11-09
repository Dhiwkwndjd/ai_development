"""
Тесты ограничений БД. Запускаются на MySQL (тестовая БД создаётся автоматически).
"""
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.catalog.models import Book, BookCopy
from apps.users.models import User

from .models import Booking, BookingStatus, QueueStatus, WaitingQueue


class BaseData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u1 = User.objects.create_user("a@example.com", "pass12345", full_name="A")
        cls.u2 = User.objects.create_user("b@example.com", "pass12345", full_name="B")
        cls.book = Book.objects.create(title="Война и мир", author="Л. Толстой", isbn="9785170000001")
        cls.copy1 = BookCopy.objects.create(book=cls.book, inventory_number="INV-1")
        cls.copy2 = BookCopy.objects.create(book=cls.book, inventory_number="INV-2")


class BookingConstraintTests(BaseData):
    def test_second_active_booking_on_same_copy_is_rejected(self):
        Booking.objects.create(user=self.u1, book_copy=self.copy1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.create(user=self.u2, book_copy=self.copy1, status=BookingStatus.CONFIRMED)

    def test_active_booking_on_other_copy_is_allowed(self):
        Booking.objects.create(user=self.u1, book_copy=self.copy1)
        Booking.objects.create(user=self.u2, book_copy=self.copy2)
        self.assertEqual(Booking.objects.active().count(), 2)

    def test_new_booking_allowed_after_previous_closed(self):
        b = Booking.objects.create(user=self.u1, book_copy=self.copy1)
        b.status = BookingStatus.CANCELLED
        b.save()
        Booking.objects.create(user=self.u2, book_copy=self.copy1)
        # история: несколько неактивных записей на один экземпляр допустимы
        Booking.objects.create(user=self.u1, book_copy=self.copy2, status=BookingStatus.COMPLETED)
        Booking.objects.create(user=self.u2, book_copy=self.copy2, status=BookingStatus.COMPLETED)

    def test_invalid_status_rejected_by_check_constraint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.create(user=self.u1, book_copy=self.copy1, status="bogus")

    def test_active_marker_is_generated(self):
        b = Booking.objects.create(user=self.u1, book_copy=self.copy1)
        b.refresh_from_db()
        self.assertEqual(b.active_marker, 1)
        b.status = BookingStatus.COMPLETED
        b.save()
        b.refresh_from_db()
        self.assertIsNone(b.active_marker)


class WaitingQueueTests(BaseData):
    def test_duplicate_active_entry_is_rejected(self):
        WaitingQueue.objects.create(user=self.u1, book=self.book)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WaitingQueue.objects.create(user=self.u1, book=self.book)

    def test_notified_still_counts_as_active(self):
        WaitingQueue.objects.create(user=self.u1, book=self.book, status=QueueStatus.NOTIFIED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WaitingQueue.objects.create(user=self.u1, book=self.book)

    def test_can_requeue_after_closing(self):
        e = WaitingQueue.objects.create(user=self.u1, book=self.book)
        e.status = QueueStatus.CANCELLED
        e.save()
        WaitingQueue.objects.create(user=self.u1, book=self.book)
        self.assertEqual(WaitingQueue.objects.filter(user=self.u1).count(), 2)

    def test_different_users_and_books_allowed(self):
        other = Book.objects.create(title="Анна Каренина", author="Л. Толстой")
        WaitingQueue.objects.create(user=self.u1, book=self.book)
        WaitingQueue.objects.create(user=self.u2, book=self.book)
        WaitingQueue.objects.create(user=self.u1, book=other)

    def test_order_and_position(self):
        e1 = WaitingQueue.objects.create(user=self.u1, book=self.book)
        e2 = WaitingQueue.objects.create(user=self.u2, book=self.book)
        self.assertEqual(list(WaitingQueue.objects.for_book(self.book)), [e1, e2])
        self.assertEqual((e1.position, e2.position), (1, 2))
        e1.status = QueueStatus.FULFILLED
        e1.save()
        self.assertIsNone(e1.position)
        self.assertEqual(e2.position, 1)
