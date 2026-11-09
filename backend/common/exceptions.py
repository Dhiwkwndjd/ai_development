from rest_framework.exceptions import APIException


class Conflict(APIException):
    """409: операция невозможна в текущем состоянии данных."""

    status_code = 409
    default_detail = "Конфликт состояния данных."
    default_code = "conflict"
