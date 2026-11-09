from audit.models import ActionLog
from bookings.models import Booking
from catalog.models import Book, BookCopy
from common.testing import ApiTestCase


class BookApiTests(ApiTestCase):
    def test_any_user_reads_but_only_staff_writes(self):
        self.make_book("Идиот", author="Достоевский")
        r = self.client_for(self.reader).get("/api/v1/books/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 1)
        payload = {"title": "Новая", "author": "Автор"}
        self.assertEqual(self.client_for(self.reader).post("/api/v1/books/", payload).status_code, 403)
        r = self.client_for(self.librarian).post("/api/v1/books/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(ActionLog.objects.filter(action="book.created").exists())

    def test_availability_counts_search_and_filters(self):
        b = self.make_book("Идиот", copies=3, author="Достоевский")
        self.make_book("Мастер и Маргарита", copies=0, author="Булгаков")
        BookCopy.objects.filter(book=b).update(status="issued")
        BookCopy.objects.filter(pk=BookCopy.objects.filter(book=b).first().pk).update(status="available")
        c = self.client_for(self.reader)
        r = c.get("/api/v1/books/?search=идиот")
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["available_copies"], 1)
        self.assertEqual(r.data["results"][0]["total_copies"], 3)
        self.assertEqual(c.get("/api/v1/books/?available=true").data["count"], 1)
        self.assertEqual(c.get("/api/v1/books/?author=булгаков").data["count"], 1)

    def test_isbn_normalized_validated_and_unique(self):
        c = self.client_for(self.librarian)
        r = c.post("/api/v1/books/", {"title": "A", "author": "B", "isbn": "978-5-17-000000-1"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["isbn"], "9785170000001")
        self.assertEqual(c.post("/api/v1/books/", {"title": "C", "author": "D", "isbn": "9785170000001"}).status_code, 400)
        self.assertEqual(c.post("/api/v1/books/", {"title": "C", "author": "D", "isbn": "12"}).status_code, 400)
        # пустой ISBN -> NULL, можно несколько
        self.assertEqual(c.post("/api/v1/books/", {"title": "E", "author": "F", "isbn": ""}).status_code, 201)
        self.assertEqual(c.post("/api/v1/books/", {"title": "G", "author": "H"}).status_code, 201)

    def test_delete_book_with_copies_is_409(self):
        b = self.make_book()
        self.assertEqual(self.client_for(self.librarian).delete(f"/api/v1/books/{b.pk}/").status_code, 409)
        empty = Book.objects.create(title="Пустая", author="X")
        self.assertEqual(self.client_for(self.librarian).delete(f"/api/v1/books/{empty.pk}/").status_code, 204)


class CopyApiTests(ApiTestCase):
    def test_readers_have_no_access(self):
        self.assertEqual(self.client_for(self.reader).get("/api/v1/copies/").status_code, 403)

    def test_create_and_manual_status_rules(self):
        b = self.make_book(copies=0)
        c = self.client_for(self.librarian)
        r = c.post("/api/v1/copies/", {"book": b.pk, "inventory_number": "INV-9"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["status"], "available")
        self.assertEqual(c.post("/api/v1/copies/", {"book": b.pk, "inventory_number": "INV-9"}).status_code, 400)
        self.assertEqual(c.post("/api/v1/copies/", {"book": b.pk, "inventory_number": "INV-10", "status": "issued"}).status_code, 400)
        r = c.patch(f"/api/v1/copies/{r.data['id']}/", {"status": "maintenance"})
        self.assertEqual(r.status_code, 200)

    def test_cannot_change_status_with_active_booking(self):
        b = self.make_book(copies=1)
        self.client_for(self.reader).post("/api/v1/bookings/", {"book": b.pk})
        copy = BookCopy.objects.get(book=b)
        r = self.client_for(self.librarian).patch(f"/api/v1/copies/{copy.pk}/", {"status": "lost"})
        self.assertEqual(r.status_code, 400)

    def test_delete_copy_with_history_is_409(self):
        b = self.make_book(copies=1)
        self.client_for(self.reader).post("/api/v1/bookings/", {"book": b.pk})
        copy = BookCopy.objects.get(book=b)
        self.assertEqual(self.client_for(self.librarian).delete(f"/api/v1/copies/{copy.pk}/").status_code, 409)
