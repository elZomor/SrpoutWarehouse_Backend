from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from inventory.models import Category, ProductType


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name"]
        read_only_fields = fields


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class ProductTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductType
        fields = ["id", "name", "model_code", "description", "category"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description", "archived"]
        read_only_fields = ["archived"]
        extra_kwargs = {
            "name": {
                "error_messages": {
                    "blank": "Name is required.",
                    "required": "Name is required.",
                },
                "validators": [
                    UniqueValidator(
                        queryset=Category.objects.all(),
                        message="A category with this name already exists.",
                    )
                ],
            },
        }
