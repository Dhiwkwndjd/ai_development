from datetime import timedelta
from unittest import mock

from django.utils import timezone

from audit.models import ActionLog
from bookings.models import Booking, WaitingQueue
from bookings.services import expire_stale_bookings, expire_stale_notifications
from catalog.models import BookCopy
from common.testing import ApiTestCase


class BookingFlowTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.book = self.make_book(copies=1)
        self.copy = BookCopy.objects.get(book=self.book)
        self.rc = self.client_for(self.reader)
        self.lc = self.client_for(self.librarian)

    def book_it(self, client=None, book=None):
        return (client or self.rc).post("/api/v1/bookings/", {"book": (book or self.book).pk})

    def test_full_lifecycle_updates_copy_status(self):
        r = self.book_it()
        self.assertEqual(r.status_code, 201, r.content)
        bid = r.data["id"]
        self.assertEqual(r.data["status"], "created")
        self.copy.refresh_from_db(); self.assertEqual(self.copy.status, "reserved")

        r = self.lc.post(f"/api/v1/bookings/{bid}/confirm/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["status"], "confirmed")
        self.assertIsNotNone(r.data["due_date"])
        self.assertEqual(r.data["processed_by"]["id"], self.librarian.pk)
        self.copy.refresh_from_db(); self.assertEqual(self.copy.status, "issued")

        r = self.lc.post(f"/api/v1/bookings/{bid}/complete/")
        self.assertEqual(r.data["status"], "completed")
        self.copy.refresh_from_db(); self.assertEqual(self.copy.status, "available")
        actions = set(ActionLog.objects.values_list("action", flat=True))
        self.assertTrue({"booking.created", "booking.confirmed", "booking.completed"} <= actions)

    def test_no_free_copy_returns_409(self):
        self.assertEqual(self.book_it().status_code, 201)
        r = self.book_it(self.client_for(self.reader2))
        self.assertEqual(r.status_code, 409)

    def test_duplicate_booking_of_same_book_rejected(self):
        b = self.make_book("Второй", copies=2)
        self.assertEqual(self.book_it(book=b).status_code, 201)
        self.assertEqual(self.book_it(book=b).status_code, 409)

    def test_active_booking_limit(self):
        for i in range(5):
            self.assertEqual(self.book_it(book=self.make_book(f"Книга{i}", copies=1)).status_code, 201)
        self.assertEqual(self.book_it(book=self.make_book("Лишняя", copies=1)).status_code, 409)

    def test_reader_sees_only_own_bookings(self):
        b2 = self.make_book("Другая", copies=1)
        self.book_it()
        self.book_it(self.client_for(self.reader2), b2)
        self.assertEqual(self.rc.get("/api/v1/bookings/").data["count"], 1)
        self.assertEqual(self.lc.get("/api/v1/bookings/").data["count"], 2)
        other = Booking.objects.get(user=self.reader2)
        self.assertEqual(self.rc.get(f"/api/v1/bookings/{other.pk}/").status_code, 404)

    def test_reader_cannot_confirm_or_book_for_others(self):
        bid = self.book_it().data["id"]
        self.assertEqual(self.rc.post(f"/api/v1/bookings/{bid}/confirm/").status_code, 403)
        r = self.rc.post("/api/v1/bookings/", {"book": self.make_book("Z").pk, "user": self.reader2.pk})
        self.assertEqual(r.status_code, 403)
        # а сотрудник — может
        r = self.lc.post("/api/v1/bookings/", {"book": self.make_book("Y").pk, "user": self.reader2.pk})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["user"]["id"], self.reader2.pk)

    def test_invalid_transitions_are_409(self):
        bid = self.book_it().data["id"]
        self.assertEqual(self.lc.post(f"/api/v1/bookings/{bid}/complete/").status_code, 409)
        self.lc.post(f"/api/v1/bookings/{bid}/confirm/")
        self.assertEqual(self.lc.post(f"/api/v1/bookings/{bid}/confirm/").status_code, 409)
        self.lc.post(f"/api/v1/bookings/{bid}/complete/")
        self.assertEqual(self.lc.post(f"/api/v1/bookings/{bid}/cancel/").status_code, 409)

    def test_confirm_due_date_in_past_rejected(self):
        bid = self.book_it().data["id"]
        past = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.assertEqual(self.lc.post(f"/api/v1/bookings/{bid}/confirm/", {"due_date": past}).status_code, 400)

    def test_reader_cancel_rules(self):
        bid = self.book_it().data["id"]
        other = self.client_for(self.reader2)
        self.assertEqual(other.post(f"/api/v1/bookings/{bid}/cancel/").status_code, 404)  # чужую не видит
        r = self.rc.post(f"/api/v1/bookings/{bid}/cancel/", {"comment": "передумал"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["status"], "cancelled")
        self.copy.refresh_from_db(); self.assertEqual(self.copy.status, "available")
        # после выдачи читатель отменить не может
        bid = self.book_it().data["id"]
        self.lc.post(f"/api/v1/bookings/{bid}/confirm/")
        self.assertEqual(self.rc.post(f"/api/v1/bookings/{bid}/cancel/").status_code, 409)
        self.assertEqual(self.lc.post(f"/api/v1/bookings/{bid}/cancel/").status_code, 200)

    def test_expire_stale_bookings_command_logic(self):
        bid = self.book_it().data["id"]
        Booking.objects.filter(pk=bid).update(created_at=timezone.now() - timedelta(hours=49))
        self.assertEqual(expire_stale_bookings(), 1)
        self.assertEqual(Booking.objects.get(pk=bid).status, "cancelled")
        self.copy.refresh_from_db(); self.assertEqual(self.copy.status, "available")


class QueueTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.book = self.make_book(copies=1)
        self.rc, self.rc2 = self.client_for(self.reader), self.client_for(self.reader2)
        self.lc = self.client_for(self.librarian)
        self.reader3 = self.make_user("reader3@example.com", "reader")
        self.rc3 = self.client_for(self.reader3)

    def occupy(self):
        """reader забронировал единственный экземпляр."""
        r = self.rc.post("/api/v1/bookings/", {"book": self.book.pk})
        self.assertEqual(r.status_code, 201)
        return r.data["id"]

    def join(self, client):
        return client.post("/api/v1/queue/", {"book": self.book.pk})

    def test_cannot_join_while_copy_free(self):
        self.assertEqual(self.join(self.rc2).status_code, 409)

    def test_join_order_positions_and_duplicate_ban(self):
        self.occupy()
        r2 = self.join(self.rc2); r3 = self.join(self.rc3)
        self.assertEqual((r2.status_code, r3.status_code), (201, 201))
        self.assertEqual((r2.data["position"], r3.data["position"]), (1, 2))
        self.assertEqual(self.join(self.rc2).status_code, 409)  # повторная активная запись запрещена
        r = self.lc.get(f"/api/v1/queue/?book={self.book.pk}&active=true")
        self.assertEqual([e["user"]["id"] for e in r.data["results"]], [self.reader2.pk, self.reader3.pk])
        self.assertEqual(self.book.queue_entries.count(), 2)

    def test_reader_with_active_booking_cannot_join(self):
        self.occupy()
        self.assertEqual(self.join(self.rc).status_code, 409)

    def test_release_notifies_first_in_line_and_protects_copy(self):
        bid = self.occupy()
        self.join(self.rc2); self.join(self.rc3)
        self.rc.post(f"/api/v1/bookings/{bid}/cancel/")  # экземпляр освободился
        e2 = WaitingQueue.objects.get(user=self.reader2)
        e3 = WaitingQueue.objects.get(user=self.reader3)
        self.assertEqual((e2.status, e3.status), ("notified", "waiting"))
        # reader3 не может «перехватить» экземпляр, закреплённый за reader2
        self.assertEqual(self.rc3.post("/api/v1/bookings/", {"book": self.book.pk}).status_code, 409)
        # reader2 бронирует — запись очереди выполнена
        r = self.rc2.post("/api/v1/bookings/", {"book": self.book.pk})
        self.assertEqual(r.status_code, 201, r.content)
        e2.refresh_from_db(); self.assertEqual(e2.status, "fulfilled")
        # после этого reader2 может снова встать в очередь (но не пока бронь активна)
        self.assertEqual(self.join(self.rc2).status_code, 409)

    def test_leaving_notified_passes_slot_on(self):
        bid = self.occupy()
        self.join(self.rc2); self.join(self.rc3)
        self.rc.post(f"/api/v1/bookings/{bid}/cancel/")
        e2 = WaitingQueue.objects.get(user=self.reader2)
        self.assertEqual(self.rc2.delete(f"/api/v1/queue/{e2.pk}/").status_code, 204)
        self.assertEqual(WaitingQueue.objects.get(user=self.reader3).status, "notified")
        self.assertEqual(WaitingQueue.objects.get(pk=e2.pk).status, "cancelled")

    def test_can_requeue_after_leaving(self):
        self.occupy()
        e = self.join(self.rc2).data
        self.assertEqual(self.rc2.delete(f"/api/v1/queue/{e['id']}/").status_code, 204)
        self.assertEqual(self.join(self.rc2).status_code, 201)

    def test_cannot_leave_foreign_entry(self):
        self.occupy()
        e = self.join(self.rc2).data
        self.assertEqual(self.rc3.delete(f"/api/v1/queue/{e['id']}/").status_code, 404)
        self.assertEqual(self.lc.delete(f"/api/v1/queue/{e['id']}/").status_code, 204)  # staff может

    def test_reader_sees_only_own_queue_entries(self):
        self.occupy()
        self.join(self.rc2); self.join(self.rc3)
        self.assertEqual(self.rc2.get("/api/v1/queue/").data["count"], 1)
        self.assertEqual(self.lc.get("/api/v1/queue/").data["count"], 2)

    def test_new_copy_or_return_from_maintenance_notifies_queue(self):
        self.occupy()
        self.join(self.rc2)
        self.lc.post("/api/v1/copies/", {"book": self.book.pk, "inventory_number": "NEW-1"})
        self.assertEqual(WaitingQueue.objects.get(user=self.reader2).status, "notified")

    def test_expired_notification_passes_to_next(self):
        bid = self.occupy()
        self.join(self.rc2); self.join(self.rc3)
        self.rc.post(f"/api/v1/bookings/{bid}/cancel/")
        WaitingQueue.objects.filter(user=self.reader2).update(notified_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(expire_stale_notifications(), 1)
        self.assertEqual(WaitingQueue.objects.get(user=self.reader2).status, "expired")
        self.assertEqual(WaitingQueue.objects.get(user=self.reader3).status, "notified")

    def test_book_list_shows_queue_length(self):
        self.occupy()
        self.join(self.rc2)
        r = self.rc.get(f"/api/v1/books/{self.book.pk}/")
        self.assertEqual((r.data["available_copies"], r.data["queue_length"]), (0, 1))
