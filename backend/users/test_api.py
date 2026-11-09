from audit.models import ActionLog
from common.testing import ApiTestCase
from tokens.models import RefreshToken
from users.models import Role, User
from rest_framework.test import APIClient


class AuthApiTests(ApiTestCase):
    def test_register_creates_reader_and_returns_tokens(self):
        r = APIClient().post("/api/v1/auth/register/", {
            "email": "New@Example.com", "password": "Str0ng-pass-123", "full_name": "New", "role": "admin",
        })
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["user"]["role"], Role.READER)  # role из запроса игнорируется
        self.assertEqual(r.data["user"]["email"], "new@example.com")
        self.assertIn("access", r.data)
        self.assertNotIn("password", r.data["user"])
        self.assertTrue(ActionLog.objects.filter(action="auth.register").exists())

    def test_register_rejects_weak_password_and_duplicate_email(self):
        c = APIClient()
        r = c.post("/api/v1/auth/register/", {"email": "x@example.com", "password": "123", "full_name": "X"})
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/v1/auth/register/", {"email": "READER@example.com", "password": "Str0ng-pass-123", "full_name": "X"})
        self.assertEqual(r.status_code, 400)

    def test_login_success_and_failure(self):
        c = APIClient()
        r = c.post("/api/v1/auth/login/", {"email": "reader@example.com", "password": "Str0ng-pass-123"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("refresh", r.data)
        r = c.post("/api/v1/auth/login/", {"email": "reader@example.com", "password": "wrong"})
        self.assertEqual(r.status_code, 401)
        self.assertTrue(ActionLog.objects.filter(action="auth.login_failed").exists())

    def test_inactive_user_cannot_login(self):
        User.objects.filter(pk=self.reader.pk).update(is_active=False)
        r = APIClient().post("/api/v1/auth/login/", {"email": "reader@example.com", "password": "Str0ng-pass-123"})
        self.assertEqual(r.status_code, 401)

    def test_protected_endpoint_requires_token(self):
        self.assertEqual(APIClient().get("/api/v1/users/me/").status_code, 401)
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION="Bearer garbage")
        self.assertEqual(c.get("/api/v1/users/me/").status_code, 401)

    def _login(self):
        r = APIClient().post("/api/v1/auth/login/", {"email": "reader@example.com", "password": "Str0ng-pass-123"})
        return r.data

    def test_refresh_rotates_token(self):
        pair = self._login()
        c = APIClient()
        r = c.post("/api/v1/auth/refresh/", {"refresh": pair["refresh"]})
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.data["refresh"], pair["refresh"])
        old = RefreshToken.objects.filter(revoked_at__isnull=False).first()
        self.assertIsNotNone(old.replaced_by)
        # новый access работает
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        self.assertEqual(c.get("/api/v1/users/me/").status_code, 200)

    def test_refresh_reuse_revokes_whole_chain(self):
        pair = self._login()
        c = APIClient()
        new = c.post("/api/v1/auth/refresh/", {"refresh": pair["refresh"]}).data
        # повторное предъявление старого токена — компрометация
        self.assertEqual(c.post("/api/v1/auth/refresh/", {"refresh": pair["refresh"]}).status_code, 401)
        # новый токен из цепочки тоже отозван
        self.assertEqual(c.post("/api/v1/auth/refresh/", {"refresh": new["refresh"]}).status_code, 401)
        self.assertTrue(ActionLog.objects.filter(action="auth.refresh_reuse_detected").exists())

    def test_logout_revokes_refresh(self):
        pair = self._login()
        c = APIClient()
        self.assertEqual(c.post("/api/v1/auth/logout/", {"refresh": pair["refresh"]}).status_code, 204)
        self.assertEqual(c.post("/api/v1/auth/refresh/", {"refresh": pair["refresh"]}).status_code, 401)

    def test_unknown_refresh_rejected(self):
        self.assertEqual(APIClient().post("/api/v1/auth/refresh/", {"refresh": "nope"}).status_code, 401)

    def test_login_is_throttled(self):
        c = APIClient()
        codes = [c.post("/api/v1/auth/login/", {"email": "reader@example.com", "password": "bad"}).status_code
                 for _ in range(25)]
        self.assertIn(429, codes)

    def test_change_password_revokes_sessions(self):
        pair = self._login()
        c = self.client_for(self.reader)
        r = c.post("/api/v1/users/me/change-password/", {"old_password": "bad", "new_password": "An0ther-pass-456"})
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/v1/users/me/change-password/", {"old_password": "Str0ng-pass-123", "new_password": "An0ther-pass-456"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(APIClient().post("/api/v1/auth/refresh/", {"refresh": pair["refresh"]}).status_code, 401)


class UsersApiTests(ApiTestCase):
    def test_me_get_and_patch_only_allowed_fields(self):
        c = self.client_for(self.reader)
        self.assertEqual(c.get("/api/v1/users/me/").data["email"], "reader@example.com")
        r = c.patch("/api/v1/users/me/", {"full_name": "Новое имя", "role": "admin", "email": "x@y.zz"})
        self.assertEqual(r.status_code, 200)
        self.reader.refresh_from_db()
        self.assertEqual(self.reader.full_name, "Новое имя")
        self.assertEqual(self.reader.role, Role.READER)
        self.assertEqual(self.reader.email, "reader@example.com")

    def test_user_list_permissions(self):
        self.assertEqual(self.client_for(self.reader).get("/api/v1/users/").status_code, 403)
        self.assertEqual(self.client_for(self.librarian).get("/api/v1/users/").status_code, 200)
        r = self.client_for(self.admin).get("/api/v1/users/?role=reader")
        self.assertEqual(r.data["count"], 2)

    def test_only_admin_can_create_and_change_roles(self):
        payload = {"email": "staff@example.com", "password": "Str0ng-pass-123", "full_name": "S", "role": "librarian"}
        self.assertEqual(self.client_for(self.librarian).post("/api/v1/users/", payload).status_code, 403)
        r = self.client_for(self.admin).post("/api/v1/users/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["role"], "librarian")
        r = self.client_for(self.admin).patch(f"/api/v1/users/{self.reader.pk}/", {"role": "librarian"})
        self.assertEqual(r.status_code, 200)

    def test_admin_cannot_demote_or_deactivate_self(self):
        c = self.client_for(self.admin)
        self.assertEqual(c.patch(f"/api/v1/users/{self.admin.pk}/", {"role": "reader"}).status_code, 400)
        self.assertEqual(c.patch(f"/api/v1/users/{self.admin.pk}/", {"is_active": False}).status_code, 400)

    def test_deactivating_user_kills_access(self):
        pair = APIClient().post("/api/v1/auth/login/", {"email": "reader@example.com", "password": "Str0ng-pass-123"}).data
        self.assertEqual(self.client_for(self.admin).patch(f"/api/v1/users/{self.reader.pk}/", {"is_active": False}).status_code, 200)
        self.assertEqual(APIClient().post("/api/v1/auth/refresh/", {"refresh": pair["refresh"]}).status_code, 401)
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}")
        self.assertEqual(c.get("/api/v1/users/me/").status_code, 401)
