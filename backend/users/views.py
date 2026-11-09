from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from common.audit import log_action
from common.permissions import IsAdminRole, IsStaffRole
from tokens.services import (
    InvalidRefreshToken,
    issue_token_pair,
    revoke_all_for_user,
    revoke_token,
    rotate_refresh_token,
)

from .models import User
from .serializers import (
    AuthResponseSerializer,
    ChangePasswordSerializer,
    LoginSerializer,
    MeSerializer,
    RefreshSerializer,
    RegisterSerializer,
    TokenPairSerializer,
    UserAdminSerializer,
    UserSerializer,
)


class _AuthBase(APIView):
    """Публичные auth-эндпоинты: без аутентификации (невалидный Bearer не мешает) и с троттлингом."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def get_authenticate_header(self, request):
        # без authenticators DRF не знает схему и вернул бы 403 вместо 401
        return "Bearer"


class RegisterView(_AuthBase):
    @extend_schema(tags=["auth"], request=RegisterSerializer, responses={201: AuthResponseSerializer})
    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        with transaction.atomic():
            user = s.save()
            log_action(request, "auth.register", user=user, entity=user)
            tokens = issue_token_pair(user, request)
        return Response({"user": UserSerializer(user).data, **tokens}, status=status.HTTP_201_CREATED)


class LoginView(_AuthBase):
    @extend_schema(tags=["auth"], request=LoginSerializer, responses={200: AuthResponseSerializer})
    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        email = s.validated_data["email"].strip().lower()
        user = User.objects.filter(email=email).first()
        if user is None or not user.is_active or not user.check_password(s.validated_data["password"]):
            log_action(request, "auth.login_failed", details={"email": email})
            raise AuthenticationFailed("Неверный email или пароль.")
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        tokens = issue_token_pair(user, request)
        log_action(request, "auth.login", user=user, entity=user)
        return Response({"user": UserSerializer(user).data, **tokens})


class RefreshView(_AuthBase):
    @extend_schema(tags=["auth"], request=RefreshSerializer, responses={200: TokenPairSerializer})
    def post(self, request):
        s = RefreshSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            user, tokens = rotate_refresh_token(s.validated_data["refresh"], request)
        except InvalidRefreshToken as exc:
            if exc.reuse_user is not None:
                log_action(request, "auth.refresh_reuse_detected", user=exc.reuse_user, entity=exc.reuse_user)
            raise AuthenticationFailed("Refresh-токен недействителен.", code="token_invalid")
        return Response(tokens)


class LogoutView(_AuthBase):
    @extend_schema(tags=["auth"], request=RefreshSerializer, responses={204: OpenApiResponse(description="OK")})
    def post(self, request):
        s = RefreshSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        revoke_token(s.validated_data["refresh"])  # идемпотентно
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutAllView(APIView):
    @extend_schema(tags=["auth"], request=None, responses={204: OpenApiResponse(description="OK")})
    def post(self, request):
        count = revoke_all_for_user(request.user)
        log_action(request, "auth.logout_all", details={"revoked": count})
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(RetrieveUpdateAPIView):
    serializer_class = MeSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user

    def perform_update(self, serializer):
        serializer.save()
        log_action(self.request, "user.profile_updated", entity=self.request.user)


class ChangePasswordView(APIView):
    @extend_schema(tags=["users"], request=ChangePasswordSerializer, responses={204: OpenApiResponse(description="OK")})
    def post(self, request):
        s = ChangePasswordSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        with transaction.atomic():
            request.user.set_password(s.validated_data["new_password"])
            request.user.save(update_fields=["password"])
            revoke_all_for_user(request.user)  # все сессии завершаются
            log_action(request, "user.password_changed", entity=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserViewSet(ModelViewSet):
    """Список/просмотр — librarian и admin. Создание, изменение — только admin. Удаления нет (блокировка через is_active)."""

    serializer_class = UserAdminSerializer
    queryset = User.objects.all()
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["email", "full_name", "phone"]
    ordering_fields = ["id", "email", "full_name", "date_joined"]
    ordering = ["id"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaffRole()]
        return [IsAdminRole()]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("role"):
            qs = qs.filter(role=p["role"])
        if p.get("is_active") in ("true", "false"):
            qs = qs.filter(is_active=p["is_active"] == "true")
        return qs

    def perform_create(self, serializer):
        user = serializer.save()
        log_action(self.request, "user.created", entity=user, details={"role": user.role})

    def perform_update(self, serializer):
        before = serializer.instance
        old_role, was_active = before.role, before.is_active
        user = serializer.save()
        changes = {}
        if user.role != old_role:
            changes["role"] = [old_role, user.role]
        if user.is_active != was_active:
            changes["is_active"] = [was_active, user.is_active]
        if not user.is_active or "password" in serializer.validated_data:
            revoke_all_for_user(user)
        log_action(self.request, "user.updated", entity=user, details=changes)
