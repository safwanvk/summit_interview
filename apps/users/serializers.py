from rest_framework import serializers
from rest_framework.validators import UniqueValidator
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """
    Full serializer for User model (used for detail view & update).
    Includes password update with confirmation.
    """
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    password_confirm = serializers.CharField(write_only=True, required=False)
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all())]
    )

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'phone_number', 'address', 'date_of_birth', 'profile_picture',
            'is_customer', 'is_vendor', 'is_active',
            'created_at', 'updated_at',
            'password', 'password_confirm'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        password = attrs.get('password')
        password_confirm = attrs.get('password_confirm')

        if password:
            if not password_confirm:
                raise serializers.ValidationError({"password_confirm": "Password confirmation is required"})
            if password != password_confirm:
                raise serializers.ValidationError({"password_confirm": "Passwords do not match"})
            validate_password(password)

        return attrs

    def create(self, validated_data):
        validated_data.pop('password_confirm', None)
        password = validated_data.pop('password', None)
        user = User.objects.create_user(**validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user

    def update(self, instance, validated_data):
        validated_data.pop('password_confirm', None)
        password = validated_data.pop('password', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            validate_password(password)
            instance.set_password(password)

        instance.save()
        return instance


class UserListSerializer(serializers.ModelSerializer):
    """
    Simplified serializer for user listing.
    """
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'full_name', 'is_customer', 'is_vendor', 'is_active']

    def get_full_name(self, obj):
        return (f"{obj.first_name or ''} {obj.last_name or ''}").strip()


class UserCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for user registration (signup).
    Includes password confirmation & validation.
    """
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all())]
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'password_confirm', 'first_name', 'last_name']

    def validate(self, attrs):
        password = attrs.get('password')
        password_confirm = attrs.get('password_confirm')

        if password != password_confirm:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match"})
        validate_password(password)
        return attrs

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = User.objects.create_user(**validated_data)
        return user
