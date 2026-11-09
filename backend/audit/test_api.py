from audit.models import ActionLog
from common.testing import ApiTestCase


class LogApiTests(ApiTestCase):
    def test_reader_forbidden_staff_allowed_readonly(self):
        self.assertEqual(self.client_for(self.reader).get("/api/v1/logs/").status_code, 403)
        c = self.client_for(self.librarian)
        self.assertEqual(c.get("/api/v1/logs/").status_code, 200)
        self.assertEqual(c.post("/api/v1/logs/", {"action": "x"}).status_code, 405)

    def test_filters(self):
        book = self.make_book()
        self.client_for(self.reader).post("/api/v1/bookings/", {"book": book.pk})
        c = self.client_for(self.admin)
        r = c.get("/api/v1/logs/?action=booking.")
        self.assertGreaterEqual(r.data["count"], 1)
        self.assertTrue(all(x["action"].startswith("booking.") for x in r.data["results"]))
        r = c.get(f"/api/v1/logs/?user={self.reader.pk}&entity_type=booking")
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["user_email"], "reader@example.com")
