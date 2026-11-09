from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("users", views.UserViewSet, basename="user")

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="auth-register"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/logout-all/", views.LogoutAllView.as_view(), name="auth-logout-all"),
    # /users/me/ объявлен ДО роутера, иначе "me" будет принят за pk
    path("users/me/", views.MeView.as_view(), name="user-me"),
    path("users/me/change-password/", views.ChangePasswordView.as_view(), name="user-change-password"),
] + router.urls
