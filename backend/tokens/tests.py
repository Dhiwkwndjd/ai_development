from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from users.models import User

from .models import RefreshToken

HASH_A = "a" * 64
HASH_B = "b" * 64


class RefreshTokenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("t@example.com", "pass12345", full_name="T")

    def test_token_hash_unique(self):
        exp = timezone.now() + timedelta(days=7)
        RefreshToken.objects.create(user=self.user, token_hash=HASH_A, expires_at=exp)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RefreshToken.objects.create(user=self.user, token_hash=HASH_A, expires_at=exp)

    def test_validity_and_rotation_chain(self):
        exp = timezone.now() + timedelta(days=7)
        old = RefreshToken.objects.create(user=self.user, token_hash=HASH_A, expires_at=exp)
        new = RefreshToken.objects.create(user=self.user, token_hash=HASH_B, expires_at=exp)
        self.assertTrue(old.is_valid)
        old.revoked_at = timezone.now()
        old.replaced_by = new
        old.save()
        self.assertFalse(old.is_valid)
        self.assertEqual(new.replaces, old)

    def test_expired_token_is_invalid(self):
        t = RefreshToken.objects.create(
            user=self.user, token_hash=HASH_A, expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertTrue(t.is_expired)
        self.assertFalse(t.is_valid)

    def test_tokens_deleted_with_user(self):
        RefreshToken.objects.create(user=self.user, token_hash=HASH_A, expires_at=timezone.now())
        self.user.delete()
        self.assertEqual(RefreshToken.objects.count(), 0)
