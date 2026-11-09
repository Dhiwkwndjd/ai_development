from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Role, User


class UserTests(TestCase):
    def test_default_role_is_reader_and_email_lowercased(self):
        u = User.objects.create_user("Reader@Example.COM", "pass12345", full_name="R")
        self.assertEqual(u.role, Role.READER)
        self.assertEqual(u.email, "reader@example.com")
        self.assertNotEqual(u.password, "pass12345")  # хранится хэш

    def test_email_unique(self):
        User.objects.create_user("x@example.com", "pass12345", full_name="X")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user("x@example.com", "pass12345", full_name="X2")

    def test_invalid_role_rejected_by_check_constraint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user("y@example.com", "pass12345", full_name="Y", role="root")

    def test_superuser_is_admin_role(self):
        su = User.objects.create_superuser("root@example.com", "pass12345", full_name="Root")
        self.assertEqual(su.role, Role.ADMIN)
        self.assertTrue(su.is_staff and su.is_superuser)
