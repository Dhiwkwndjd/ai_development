from rest_framework.permissions import SAFE_METHODS, BasePermission

from users.models import Role

STAFF_ROLES = (Role.LIBRARIAN, Role.ADMIN)


def is_staff_role(user) -> bool:
    return bool(user and user.is_authenticated and user.role in STAFF_ROLES)


class IsStaffRole(BasePermission):
    """librarian или admin."""

    message = "Требуется роль librarian или admin."

    def has_permission(self, request, view):
        return is_staff_role(request.user)


class IsAdminRole(BasePermission):
    message = "Требуется роль admin."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role == Role.ADMIN)


class ReadOnlyOrStaff(BasePermission):
    """Чтение — любому авторизованному, изменение — librarian/admin."""

    message = "Изменять данные может только librarian или admin."

    def has_permission(self, request, view):
        u = request.user
        if not (u and u.is_authenticated):
            return False
        return request.method in SAFE_METHODS or is_staff_role(u)
