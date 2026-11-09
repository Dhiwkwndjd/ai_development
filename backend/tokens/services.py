import hashlib
import secrets

import jwt
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import RefreshToken


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_access_token(user) -> str:
    now = timezone.now()
    payload = {
        "sub": str(user.pk),
        "role": user.role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + settings.JWT["ACCESS_LIFETIME"]).timestamp()),
    }
    return jwt.encode(payload, settings.JWT["SIGNING_KEY"], algorithm="HS256")


def _client_meta(request):
    if request is None:
        return {}
    return {
        "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:255],
        "ip_address": request.META.get("REMOTE_ADDR") or None,
    }


def _create_refresh(user, request) -> tuple[str, RefreshToken]:
    raw = secrets.token_urlsafe(48)
    obj = RefreshToken.objects.create(
        user=user,
        token_hash=hash_token(raw),
        expires_at=timezone.now() + settings.JWT["REFRESH_LIFETIME"],
        **_client_meta(request),
    )
    return raw, obj


def _pair(user, refresh_raw: str) -> dict:
    return {
        "access": create_access_token(user),
        "refresh": refresh_raw,
        "token_type": "Bearer",
        "expires_in": int(settings.JWT["ACCESS_LIFETIME"].total_seconds()),
    }


def issue_token_pair(user, request=None) -> dict:
    raw, _ = _create_refresh(user, request)
    return _pair(user, raw)


class InvalidRefreshToken(Exception):
    def __init__(self, reuse_user=None):
        self.reuse_user = reuse_user  # не None — обнаружено повторное использование


def rotate_refresh_token(raw: str, request=None):
    """
    Обменивает refresh-токен на новую пару; старый отзывается.
    Повторное предъявление уже отозванного токена считается компрометацией:
    все токены пользователя отзываются.
    """
    reuse_user = None
    result = None
    with transaction.atomic():
        obj = (
            RefreshToken.objects.select_for_update()
            .select_related("user")
            .filter(token_hash=hash_token(raw))
            .first()
        )
        if obj is None:
            raise InvalidRefreshToken()
        if obj.revoked_at is not None:
            revoke_all_for_user(obj.user)
            reuse_user = obj.user
        elif obj.is_expired or not obj.user.is_active:
            raise InvalidRefreshToken()
        else:
            new_raw, new_obj = _create_refresh(obj.user, request)
            now = timezone.now()
            obj.revoked_at = now
            obj.last_used_at = now
            obj.replaced_by = new_obj
            obj.save(update_fields=["revoked_at", "last_used_at", "replaced_by"])
            result = (obj.user, _pair(obj.user, new_raw))
    if reuse_user is not None:  # вне atomic, чтобы отзыв токенов закоммитился
        raise InvalidRefreshToken(reuse_user=reuse_user)
    return result


def revoke_token(raw: str) -> None:
    RefreshToken.objects.filter(token_hash=hash_token(raw), revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def revoke_all_for_user(user) -> int:
    return RefreshToken.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
