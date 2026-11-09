from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import Role, User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "full_name", "phone", "role", "is_active", "date_joined"]
        read_only_fields = fields


class UserBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "full_name"]
        read_only_fields = fields


class PersonSerializer(serializers.ModelSerializer):
    """Без контактов — для показа другим пользователям."""

    class Meta:
        model = User
        fields = ["id", "full_name"]
        read_only_fields = fields


class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "full_name", "phone", "role", "is_active", "date_joined"]
        read_only_fields = ["id", "email", "role", "is_active", "date_joined"]


def _unique_email(value: str) -> str:
    value = value.strip().lower()
    if User.objects.filter(email=value).exists():
        raise serializers.ValidationError("Пользователь с таким email уже существует.")
    return value


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ["email", "password", "full_name", "phone"]
        extra_kwargs = {"phone": {"required": False}, "email": {"validators": []}}

    def validate_email(self, value):
        return _unique_email(value)

    def validate(self, attrs):
        candidate = User(email=attrs.get("email"), full_name=attrs.get("full_name", ""))
        validate_password(attrs["password"], candidate)
        return attrs

    def create(self, validated_data):
        # Саморегистрация всегда создаёт читателя.
        return User.objects.create_user(role=Role.READER, **validated_data)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    token_type = serializers.CharField()
    expires_in = serializers.IntegerField()


class AuthResponseSerializer(TokenPairSerializer):
    user = UserSerializer()


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Неверный текущий пароль.")
        return value

    def validate_new_password(self, value):
        validate_password(value, self.context["request"].user)
        return value


class UserAdminSerializer(serializers.ModelSerializer):
    """Управление пользователями (admin): создание сотрудников, смена роли, блокировка."""

    password = serializers.CharField(write_only=True, required=False, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ["id", "email", "password", "full_name", "phone", "role", "is_active", "date_joined"]
        read_only_fields = ["id", "date_joined"]
        extra_kwargs = {"email": {"validators": []}}

    def validate_email(self, value):
        value = value.strip().lower()
        qs = User.objects.filter(email=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Пользователь с таким email уже существует.")
        return value

    def validate_password(self, value):
        validate_password(value, self.instance)
        return value

    def validate(self, attrs):
        if self.instance is None and "password" not in attrs:
            raise serializers.ValidationError({"password": "Обязательное поле."})
        request = self.context["request"]
        if self.instance is not None and self.instance.pk == request.user.pk:
            if "role" in attrs and attrs["role"] != self.instance.role:
                raise serializers.ValidationError({"role": "Нельзя менять собственную роль."})
            if attrs.get("is_active") is False:
                raise serializers.ValidationError({"is_active": "Нельзя отключить самого себя."})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        role = validated_data.get("role", Role.READER)
        validated_data["is_staff"] = role == Role.ADMIN
        return User.objects.create_user(password=password, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        if "role" in validated_data:
            instance.is_staff = instance.role == Role.ADMIN
        if password:
            instance.set_password(password)
        instance.save()
        return instance
