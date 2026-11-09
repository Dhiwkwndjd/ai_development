from rest_framework import serializers

from .models import ActionLog


class ActionLogSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True, default=None)

    class Meta:
        model = ActionLog
        fields = [
            "id", "created_at", "user", "user_email", "action", "entity_type", "entity_id",
            "details", "ip_address", "user_agent",
        ]
        read_only_fields = fields
