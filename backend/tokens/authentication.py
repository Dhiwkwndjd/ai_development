import jwt
from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from users.models import User


class JWTAuthentication(BaseAuthentication):
    """Authorization: Bearer <access JWT>."""

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != b"bearer":
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Неверный формат заголовка Authorization.")
        try:
            payload = jwt.decode(parts[1], settings.JWT["SIGNING_KEY"], algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed("Access-токен истёк.", code="token_expired")
        except jwt.InvalidTokenError:
            raise AuthenticationFailed("Недействительный токен.", code="token_invalid")
        if payload.get("type") != "access":
            raise AuthenticationFailed("Недействительный токен.", code="token_invalid")
        user = User.objects.filter(pk=payload.get("sub"), is_active=True).first()
        if user is None:
            raise AuthenticationFailed("Пользователь не найден или отключён.", code="user_inactive")
        return user, payload

    def authenticate_header(self, request):
        return "Bearer"


class JWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.tokens.authentication.JWTAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
