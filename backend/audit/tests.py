from django.test import TestCase

from apps.users.models import User

from .models import ActionLog


class ActionLogTests(TestCase):
    def test_log_survives_user_deletion(self):
        u = User.objects.create_user("l@example.com", "pass12345", full_name="L")
        log = ActionLog.objects.create(
            user=u, action="auth.login", entity_type="user", entity_id=u.pk, details={"ok": True}
        )
        u.delete()
        log.refresh_from_db()
        self.assertIsNone(log.user)
        self.assertEqual(log.details, {"ok": True})

    def test_anonymous_action_allowed(self):
        ActionLog.objects.create(action="auth.login_failed", ip_address="127.0.0.1")
        self.assertEqual(ActionLog.objects.count(), 1)
