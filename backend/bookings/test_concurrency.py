"""Гонки на реальной БД: несколько потоков одновременно бронируют последний экземпляр."""
import threading

from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from apps.bookings.models import Booking, WaitingQueue
from apps.catalog.models import Book, BookCopy
from apps.tokens.services import create_access_token
from apps.users.models import User


class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.book = Book.objects.create(title="Единственная", author="А")
        BookCopy.objects.create(book=self.book, inventory_number="ONLY-1")
        self.users = [
            User.objects.create_user(f"u{i}@example.com", "Str0ng-pass-123", full_name=f"U{i}") for i in range(6)
        ]

    def _run_parallel(self, fn):
        results, barrier = [], threading.Barrier(len(self.users))

        def worker(user):
            try:
                c = APIClient()
                c.credentials(HTTP_AUTHORIZATION=f"Bearer {create_access_token(user)}")
                barrier.wait()
                results.append(fn(c).status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(u,)) for u in self.users]
        [t.start() for t in threads]
        [t.join() for t in threads]
        return results

    def test_last_copy_is_booked_exactly_once(self):
        codes = self._run_parallel(lambda c: c.post("/api/v1/bookings/", {"book": self.book.pk}))
        self.assertEqual(codes.count(201), 1, codes)
        self.assertEqual(Booking.objects.active().count(), 1)

    def test_same_user_cannot_double_queue_in_parallel(self):
        BookCopy.objects.update(status="issued")
        user = self.users[0]
        self.users = [user] * 6  # один и тот же пользователь шлёт 6 запросов одновременно
        codes = self._run_parallel(lambda c: c.post("/api/v1/queue/", {"book": self.book.pk}))
        self.assertEqual(codes.count(201), 1, codes)
        self.assertEqual(WaitingQueue.objects.filter(user=user).count(), 1)
