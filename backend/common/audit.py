from audit.models import ActionLog


def log_action(request, action, *, user=None, entity=None, entity_type="", entity_id=None, details=None):
    """Запись в журнал действий. `entity` — любой объект модели Django."""
    ip = ua = None
    if request is not None:
        ip = request.META.get("REMOTE_ADDR") or None
        ua = (request.META.get("HTTP_USER_AGENT") or "")[:255]
        if user is None:
            u = getattr(request, "user", None)
            if u is not None and u.is_authenticated:
                user = u
    if entity is not None:
        entity_type = entity._meta.model_name
        entity_id = entity.pk
    return ActionLog.objects.create(
        user=user,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
        ip_address=ip,
        user_agent=ua or "",
    )
