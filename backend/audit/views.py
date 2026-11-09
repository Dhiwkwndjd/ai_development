from django.utils.dateparse import parse_datetime
from rest_framework.filters import OrderingFilter
from rest_framework.viewsets import ReadOnlyModelViewSet

from common.permissions import IsStaffRole

from .models import ActionLog
from .serializers import ActionLogSerializer


class ActionLogViewSet(ReadOnlyModelViewSet):
    """
    Журнал действий (только чтение, librarian/admin).
    Фильтры: ?user=, ?action= (точное или префикс "booking."), ?entity_type=, ?entity_id=,
    ?from= / ?to= (ISO 8601).
    """

    serializer_class = ActionLogSerializer
    permission_classes = [IsStaffRole]
    queryset = ActionLog.objects.select_related("user")
    filter_backends = [OrderingFilter]
    ordering_fields = ["id", "created_at"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("user", "").isdigit():
            qs = qs.filter(user_id=int(p["user"]))
        if p.get("action"):
            a = p["action"]
            qs = qs.filter(action__startswith=a) if a.endswith(".") else qs.filter(action=a)
        if p.get("entity_type"):
            qs = qs.filter(entity_type=p["entity_type"])
        if p.get("entity_id", "").isdigit():
            qs = qs.filter(entity_id=int(p["entity_id"]))
        if p.get("from") and (dt := parse_datetime(p["from"])):
            qs = qs.filter(created_at__gte=dt)
        if p.get("to") and (dt := parse_datetime(p["to"])):
            qs = qs.filter(created_at__lte=dt)
        return qs
